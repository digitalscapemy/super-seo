<?php

namespace App\Services\Seo;

use App\Models\SeoIntegration;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;
use RuntimeException;

/**
 * Google OAuth 2.0 (web flow + PKCE) and service-account access tokens, without the Google SDK.
 *
 * config/services.php:
 *   'google_seo' => [
 *       'client_id' => env('GOOGLE_SEO_CLIENT_ID'),
 *       'client_secret' => env('GOOGLE_SEO_CLIENT_SECRET'),
 *       'redirect' => env('GOOGLE_SEO_REDIRECT_URI'),            // exact URI registered in Google Cloud
 *       'service_account_json' => env('GOOGLE_SEO_SERVICE_ACCOUNT_JSON'), // path to the key file, outside the web root
 *   ],
 */
class GoogleAuth
{
    public const SCOPES = [
        'openid', 'email',
        'https://www.googleapis.com/auth/webmasters.readonly',
        'https://www.googleapis.com/auth/analytics.readonly',
    ];

    /** Step 1: build the consent URL and remember state + PKCE verifier in the session. */
    public function authorizationUrl(?int $tenantId): string
    {
        $state = Str::random(48);
        $verifier = Str::random(96);
        session()->put('google_seo_oauth', [
            'state' => $state, 'verifier' => $verifier, 'tenant_id' => $tenantId,
            'user_id' => auth()->id(), 'expires' => now()->addMinutes(10)->timestamp,
        ]);
        $challenge = rtrim(strtr(base64_encode(hash('sha256', $verifier, true)), '+/', '-_'), '=');

        return 'https://accounts.google.com/o/oauth2/v2/auth?' . http_build_query([
            'response_type' => 'code',
            'client_id' => config('services.google_seo.client_id'),
            'redirect_uri' => config('services.google_seo.redirect'),
            'scope' => implode(' ', self::SCOPES),
            'state' => $state,
            'code_challenge' => $challenge,
            'code_challenge_method' => 'S256',
            'access_type' => 'offline',
            'include_granted_scopes' => 'true',
            'prompt' => 'consent',
        ]);
    }

    /** Step 2: validate state (single use, same admin, not expired) and exchange the code. */
    public function handleCallback(string $code, string $state): array
    {
        $saved = session()->pull('google_seo_oauth');
        if (! $saved || ! hash_equals($saved['state'], $state) || $saved['user_id'] !== auth()->id() || $saved['expires'] < time()) {
            throw new RuntimeException('Invalid or expired OAuth state.');
        }
        $res = Http::asForm()->timeout(15)->post('https://oauth2.googleapis.com/token', [
            'grant_type' => 'authorization_code',
            'code' => $code,
            'code_verifier' => $saved['verifier'],
            'client_id' => config('services.google_seo.client_id'),
            'client_secret' => config('services.google_seo.client_secret'),
            'redirect_uri' => config('services.google_seo.redirect'),
        ])->throw()->json();

        $granted = explode(' ', $res['scope'] ?? '');
        if (! in_array('https://www.googleapis.com/auth/webmasters.readonly', $granted, true)) {
            throw new RuntimeException('Search Console permission was not granted. Please tick it on the consent screen.');
        }
        if (empty($res['refresh_token'])) {
            throw new RuntimeException('Google did not return a refresh token. Disconnect and connect again.');
        }
        $email = null;
        if (! empty($res['id_token'])) {   // came straight from Google's token endpoint over TLS; decode only for the email
            $payload = json_decode(base64_decode(strtr(explode('.', $res['id_token'])[1] ?? '', '-_', '+/')), true);
            $email = $payload['email'] ?? null;
        }

        return ['tenant_id' => $saved['tenant_id'], 'refresh_token' => $res['refresh_token'],
                'scopes' => $res['scope'] ?? '', 'email' => $email];
    }

    /** Access token for an integration (cached until shortly before expiry). */
    public function accessToken(SeoIntegration $i): string
    {
        return Cache::remember("google_seo_at:{$i->id}", now()->addMinutes(50), function () use ($i) {
            return $i->auth_type === 'service_account' ? $this->serviceAccountToken() : $this->refresh($i);
        });
    }

    private function refresh(SeoIntegration $i): string
    {
        $res = Http::asForm()->timeout(15)->post('https://oauth2.googleapis.com/token', [
            'grant_type' => 'refresh_token',
            'refresh_token' => $i->refresh_token,
            'client_id' => config('services.google_seo.client_id'),
            'client_secret' => config('services.google_seo.client_secret'),
        ]);
        if ($res->status() === 400 && ($res->json('error') === 'invalid_grant')) {
            $i->update(['status' => 'reconnect_required', 'last_error' => 'Google access was revoked or expired. Reconnect.']);
            throw new RuntimeException('reconnect_required');
        }

        return $res->throw()->json('access_token');
    }

    /** Service account: signed JWT -> access token (platform mode). */
    private function serviceAccountToken(): string
    {
        $key = json_decode(file_get_contents(config('services.google_seo.service_account_json')), true);
        $b64 = fn ($v) => rtrim(strtr(base64_encode(is_string($v) ? $v : json_encode($v)), '+/', '-_'), '=');
        $now = time();
        $unsigned = $b64(['alg' => 'RS256', 'typ' => 'JWT']) . '.' . $b64([
            'iss' => $key['client_email'],
            'scope' => 'https://www.googleapis.com/auth/webmasters.readonly https://www.googleapis.com/auth/analytics.readonly',
            'aud' => 'https://oauth2.googleapis.com/token',
            'iat' => $now, 'exp' => $now + 3600,
        ]);
        openssl_sign($unsigned, $sig, $key['private_key'], OPENSSL_ALGO_SHA256);

        return Http::asForm()->timeout(15)->post('https://oauth2.googleapis.com/token', [
            'grant_type' => 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion' => $unsigned . '.' . $b64($sig),
        ])->throw()->json('access_token');
    }

    public function revoke(SeoIntegration $i): void
    {
        if ($i->auth_type === 'oauth' && $i->refresh_token) {
            Http::asForm()->timeout(10)->post('https://oauth2.googleapis.com/revoke', ['token' => $i->refresh_token]);
        }
        Cache::forget("google_seo_at:{$i->id}");
    }
}
