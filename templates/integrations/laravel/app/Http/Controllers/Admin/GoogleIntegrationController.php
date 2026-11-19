<?php

namespace App\Http\Controllers\Admin;

use App\Http\Controllers\Controller;
use App\Jobs\SyncSearchConsole;
use App\Models\SeoIntegration;
use App\Services\Seo\GoogleAuth;
use App\Services\Seo\SearchConsole;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Throwable;

/**
 * routes/web.php (inside the super-admin group: auth + can:manage-seo-integrations middleware):
 *   Route::get('integrations/google/connect', [GoogleIntegrationController::class, 'connect'])->name('admin.google.connect');
 *   Route::get('integrations/google/callback', [GoogleIntegrationController::class, 'callback'])->name('admin.google.callback');
 *   Route::post('integrations/google/property', [GoogleIntegrationController::class, 'property'])->name('admin.google.property');
 *   Route::delete('integrations/google', [GoogleIntegrationController::class, 'disconnect'])->name('admin.google.disconnect');
 */
class GoogleIntegrationController extends Controller
{
    public function __construct(private GoogleAuth $auth, private SearchConsole $gsc) {}

    /** Which tenant the admin is managing. NULL = platform. Replace with your tenancy resolver. */
    private function tenantId(Request $r): ?int
    {
        return $r->user()->isSuperAdmin() && $r->filled('tenant') ? (int) $r->input('tenant') : $r->user()->tenant_id;
    }

    public function connect(Request $r)
    {
        return redirect()->away($this->auth->authorizationUrl($this->tenantId($r)));
    }

    public function callback(Request $r)
    {
        if ($r->filled('error')) {
            return redirect()->route('admin.integrations')->with('error', 'Google connection was cancelled.');
        }
        try {
            $res = $this->auth->handleCallback((string) $r->query('code'), (string) $r->query('state'));
        } catch (Throwable $e) {
            Log::warning('google_seo_oauth_failed', ['user' => $r->user()->id, 'error' => $e->getMessage()]);

            return redirect()->route('admin.integrations')->with('error', $e->getMessage());
        }
        $i = SeoIntegration::updateOrCreate(
            ['tenant_id' => $res['tenant_id'], 'provider' => 'google'],
            ['auth_type' => 'oauth', 'refresh_token' => $res['refresh_token'], 'scopes' => $res['scopes'],
             'account_email' => $res['email'], 'status' => 'pending', 'connected_by' => $r->user()->id, 'last_error' => null]
        );
        Log::info('google_seo_connected', ['integration' => $i->id, 'tenant' => $i->tenant_id, 'by' => $r->user()->id]); // audit trail

        return redirect()->route('admin.integrations', ['pick_property' => 1]);
    }

    /** Property picker submit. Validates ownership against the tenant's verified domains. */
    public function property(Request $r)
    {
        $data = $r->validate(['property' => ['required', 'string', 'max:255']]);
        $tenantId = $this->tenantId($r);
        $i = SeoIntegration::forTenant($tenantId)->where('provider', 'google')->firstOrFail();

        $visible = collect($this->gsc->sites($i))->pluck('siteUrl');
        $domains = $tenantId === null ? [parse_url(config('app.url'), PHP_URL_HOST)] : $this->verifiedDomainsFor($tenantId);
        abort_unless($visible->contains($data['property']) && SearchConsole::propertyMatchesDomains($data['property'], $domains), 422,
            'That Search Console property does not match this site\'s domain.');

        $i->update(['gsc_property' => $data['property'], 'status' => 'active']);
        SyncSearchConsole::dispatch($i->id);   // starts the 16-month backfill

        return back()->with('status', 'Search Console connected. Importing history in the background.');
    }

    public function disconnect(Request $r)
    {
        $i = SeoIntegration::forTenant($this->tenantId($r))->where('provider', 'google')->firstOrFail();
        $this->auth->revoke($i);
        $i->update(['refresh_token' => null, 'status' => 'disconnected']);
        if ($r->boolean('delete_data')) {
            $i->delete();   // cascades synced rows
        }

        return back()->with('status', 'Google disconnected.');
    }

    private function verifiedDomainsFor(int $tenantId): array
    {
        // Replace with your model: the tenant's primary domain + verified custom domains.
        return \App\Models\Domain::where('tenant_id', $tenantId)->where('verified', true)->pluck('host')->all();
    }
}
