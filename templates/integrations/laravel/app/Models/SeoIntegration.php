<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class SeoIntegration extends Model
{
    protected $guarded = ['id'];

    // Secrets are encrypted at rest with APP_KEY and never serialised to JSON / the browser.
    protected $hidden = ['refresh_token', 'api_key'];

    protected function casts(): array
    {
        return [
            'refresh_token' => 'encrypted',
            'api_key' => 'encrypted',
            'last_synced_at' => 'datetime',
            'backfilled_at' => 'datetime',
        ];
    }

    /** Always scope by tenant. NULL tenant = the platform's own site (super admin only). */
    public function scopeForTenant($query, ?int $tenantId)
    {
        return $tenantId === null ? $query->whereNull('tenant_id') : $query->where('tenant_id', $tenantId);
    }

    public function isActive(): bool
    {
        return $this->status === 'active';
    }
}
