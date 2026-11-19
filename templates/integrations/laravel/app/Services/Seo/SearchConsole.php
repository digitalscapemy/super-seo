<?php

namespace App\Services\Seo;

use App\Models\SeoIntegration;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;

/** Google Search Console API (v3 webmasters + v1 URL Inspection). */
class SearchConsole
{
    public function __construct(private GoogleAuth $auth) {}

    private function http(SeoIntegration $i): PendingRequest
    {
        return Http::withToken($this->auth->accessToken($i))
            ->acceptJson()->timeout(60)
            // 429 / 5xx: exponential backoff 1,2,4,8,16 s
            ->retry(5, fn (int $attempt) => (2 ** ($attempt - 1)) * 1000 + random_int(0, 500),
                fn ($e) => in_array(optional($e->response ?? null)->status(), [429, 500, 502, 503, 504], true), throw: true);
    }

    private static function site(string $property): string
    {
        return rawurlencode($property);   // sc-domain:example.com -> sc-domain%3Aexample.com
    }

    /** Properties the connected account can see (for the property picker). */
    public function sites(SeoIntegration $i): array
    {
        return collect($this->http($i)->get('https://www.googleapis.com/webmasters/v3/sites')->json('siteEntry', []))
            ->filter(fn ($s) => ($s['permissionLevel'] ?? '') !== 'siteUnverifiedUser')
            ->values()->all();
    }

    /** Search Analytics with pagination (25k rows per page). Yields rows. */
    public function query(SeoIntegration $i, string $start, string $end, array $dimensions, string $dataState = 'final'): \Generator
    {
        $startRow = 0;
        do {
            $rows = $this->http($i)->post(
                'https://www.googleapis.com/webmasters/v3/sites/' . self::site($i->gsc_property) . '/searchAnalytics/query',
                ['startDate' => $start, 'endDate' => $end, 'dimensions' => $dimensions, 'type' => 'web',
                 'dataState' => $dataState, 'rowLimit' => 25000, 'startRow' => $startRow]
            )->json('rows', []);
            foreach ($rows as $r) {
                yield $r;
            }
            $startRow += 25000;
        } while (count($rows) === 25000);
    }

    public function sitemaps(SeoIntegration $i): array
    {
        return $this->http($i)->get('https://www.googleapis.com/webmasters/v3/sites/' . self::site($i->gsc_property) . '/sitemaps')
            ->json('sitemap', []);
    }

    /** URL Inspection (2,000/day/property): call for key URLs only, cache results. */
    public function inspect(SeoIntegration $i, string $url): array
    {
        $r = $this->http($i)->post('https://searchconsole.googleapis.com/v1/urlInspection/index:inspect', [
            'inspectionUrl' => $url, 'siteUrl' => $i->gsc_property,
        ])->json('inspectionResult.indexStatusResult', []);

        return [
            'verdict' => $r['verdict'] ?? null,
            'coverage_state' => $r['coverageState'] ?? null,
            'google_canonical' => $r['googleCanonical'] ?? null,
            'user_canonical' => $r['userCanonical'] ?? null,
            'robots_state' => $r['robotsTxtState'] ?? null,
            'indexing_state' => $r['indexingState'] ?? null,
            'last_crawl_at' => isset($r['lastCrawlTime']) ? \Carbon\Carbon::parse($r['lastCrawlTime']) : null,
        ];
    }

    /**
     * Only allow a property whose host EXACTLY equals one of the tenant's verified domains (www ignored).
     * Never accept a parent-domain property for a tenant: sc-domain:platform.com would expose every other
     * tenant's subdomain data. Only the platform itself (tenant NULL) may use its parent Domain property.
     */
    public static function propertyMatchesDomains(string $property, array $verifiedDomains): bool
    {
        $host = str_starts_with($property, 'sc-domain:')
            ? substr($property, 10)
            : (parse_url($property, PHP_URL_HOST) ?? '');
        $host = strtolower(preg_replace('/^www\./', '', $host));
        if ($host === '') {
            return false;
        }
        foreach ($verifiedDomains as $d) {
            if ($host === strtolower(preg_replace('/^www\./', '', (string) $d))) {
                return true;
            }
        }

        return false;
    }
}
