<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('seo_integrations', function (Blueprint $t) {
            $t->id();
            $t->foreignId('tenant_id')->nullable()->index();            // NULL = the platform's own site
            $t->string('provider', 20);                                 // google | bing | indexnow | psi
            $t->string('auth_type', 20);                                // oauth | service_account | api_key
            $t->string('account_email')->nullable();
            $t->string('gsc_property')->nullable();                     // sc-domain:example.com | https://example.com/
            $t->string('ga4_property_id')->nullable();
            $t->string('site_url')->nullable();                         // for Bing / IndexNow / PSI
            $t->text('scopes')->nullable();
            $t->text('refresh_token')->nullable();                      // encrypted cast
            $t->text('api_key')->nullable();                            // encrypted cast
            $t->string('status', 30)->default('pending');               // pending|active|reconnect_required|error|disconnected
            $t->timestamp('last_synced_at')->nullable();
            $t->timestamp('backfilled_at')->nullable();
            $t->text('last_error')->nullable();
            $t->foreignId('connected_by')->nullable();
            $t->timestamps();
            $t->unique(['tenant_id', 'provider']);
        });

        Schema::create('gsc_daily', function (Blueprint $t) {
            $t->foreignId('integration_id')->constrained('seo_integrations')->cascadeOnDelete();
            $t->date('date');
            $t->unsignedInteger('clicks');
            $t->unsignedInteger('impressions');
            $t->decimal('ctr', 6, 5);
            $t->decimal('position', 7, 2);
            $t->primary(['integration_id', 'date']);
        });

        Schema::create('gsc_page_query', function (Blueprint $t) {
            $t->id();
            $t->foreignId('integration_id')->constrained('seo_integrations')->cascadeOnDelete();
            $t->date('date');
            $t->text('page');
            $t->text('query');
            $t->char('page_hash', 40);
            $t->char('query_hash', 40);
            $t->char('country', 3)->default('');
            $t->string('device', 10)->default('');
            $t->unsignedInteger('clicks');
            $t->unsignedInteger('impressions');
            $t->decimal('ctr', 6, 5);
            $t->decimal('position', 7, 2);
            $t->unique(['integration_id', 'date', 'page_hash', 'query_hash', 'country', 'device'], 'gsc_pq_unique');
            $t->index(['integration_id', 'date']);
        });

        Schema::create('gsc_url_inspections', function (Blueprint $t) {
            $t->id();
            $t->foreignId('integration_id')->constrained('seo_integrations')->cascadeOnDelete();
            $t->text('url');
            $t->char('url_hash', 40);
            $t->string('verdict', 30)->nullable();                      // PASS | NEUTRAL | FAIL
            $t->string('coverage_state')->nullable();
            $t->text('google_canonical')->nullable();
            $t->text('user_canonical')->nullable();
            $t->string('robots_state', 40)->nullable();
            $t->string('indexing_state', 40)->nullable();
            $t->timestamp('last_crawl_at')->nullable();
            $t->timestamp('inspected_at');
            $t->unique(['integration_id', 'url_hash']);
        });

        Schema::create('seo_vitals', function (Blueprint $t) {
            $t->id();
            $t->foreignId('integration_id')->constrained('seo_integrations')->cascadeOnDelete();
            $t->string('scope', 10);                                    // origin | url
            $t->text('url');
            $t->string('form_factor', 10)->default('PHONE');
            $t->unsignedInteger('lcp_p75')->nullable();                 // ms
            $t->unsignedInteger('inp_p75')->nullable();                 // ms
            $t->decimal('cls_p75', 5, 3)->nullable();
            $t->string('source', 10);                                   // crux | psi
            $t->timestamp('measured_at');
        });

        Schema::create('indexnow_queue', function (Blueprint $t) {
            $t->id();
            $t->string('host');
            $t->text('url');
            $t->timestamp('queued_at');
            $t->timestamp('sent_at')->nullable();
            $t->unsignedSmallInteger('response_code')->nullable();
            $t->index(['host', 'sent_at']);
        });
    }

    public function down(): void
    {
        foreach (['indexnow_queue', 'seo_vitals', 'gsc_url_inspections', 'gsc_page_query', 'gsc_daily', 'seo_integrations'] as $table) {
            Schema::dropIfExists($table);
        }
    }
};
