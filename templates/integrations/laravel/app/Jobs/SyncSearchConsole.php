<?php

namespace App\Jobs;

use App\Models\SeoIntegration;
use App\Services\Seo\SearchConsole;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldBeUnique;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Support\Facades\DB;
use Throwable;

/**
 * Daily: upsert the last 3 final days (GSC data lags ~2-3 days). First run: backfill 16 months, month by month.
 * Scheduler (routes/console.php):
 *   Schedule::call(fn () => SeoIntegration::where('provider','google')->where('status','active')->whereNotNull('gsc_property')
 *       ->each(fn ($i) => SyncSearchConsole::dispatch($i->id)))->dailyAt('03:00');
 */
class SyncSearchConsole implements ShouldQueue, ShouldBeUnique
{
    use Dispatchable, InteractsWithQueue, Queueable;

    public int $tries = 3;
    public int $timeout = 1800;
    public array $backoff = [60, 300, 900];

    public function __construct(public int $integrationId) {}

    public function uniqueId(): string
    {
        return (string) $this->integrationId;   // one sync per property at a time (quotas are per property)
    }

    public function handle(SearchConsole $gsc): void
    {
        $i = SeoIntegration::findOrFail($this->integrationId);
        if (! $i->isActive() || ! $i->gsc_property) {
            return;
        }
        try {
            $ranges = $i->backfilled_at
                ? [[now()->subDays(4)->toDateString(), now()->subDays(2)->toDateString()]]
                : collect(range(16, 0))->map(fn ($m) => [
                    now()->subMonths($m)->startOfMonth()->toDateString(),
                    min(now()->subMonths($m)->endOfMonth(), now()->subDays(2))->toDateString(),
                ])->all();

            foreach ($ranges as [$start, $end]) {
                $this->syncDaily($gsc, $i, $start, $end);
                $this->syncPageQuery($gsc, $i, $start, $end);
            }
            $i->update(['last_synced_at' => now(), 'backfilled_at' => $i->backfilled_at ?? now(), 'last_error' => null]);
        } catch (Throwable $e) {
            if ($e->getMessage() !== 'reconnect_required') {
                $i->update(['last_error' => mb_substr($e->getMessage(), 0, 500)]);   // never log tokens
            }
            throw $e;
        }
    }

    private function syncDaily(SearchConsole $gsc, SeoIntegration $i, string $start, string $end): void
    {
        $rows = [];
        foreach ($gsc->query($i, $start, $end, ['date']) as $r) {
            $rows[] = ['integration_id' => $i->id, 'date' => $r['keys'][0], 'clicks' => (int) $r['clicks'],
                       'impressions' => (int) $r['impressions'], 'ctr' => $r['ctr'], 'position' => $r['position']];
        }
        DB::table('gsc_daily')->upsert($rows, ['integration_id', 'date'], ['clicks', 'impressions', 'ctr', 'position']);
    }

    private function syncPageQuery(SearchConsole $gsc, SeoIntegration $i, string $start, string $end): void
    {
        $batch = [];
        foreach ($gsc->query($i, $start, $end, ['date', 'page', 'query', 'country', 'device']) as $r) {
            [$date, $page, $query, $country, $device] = $r['keys'];
            $batch[] = ['integration_id' => $i->id, 'date' => $date, 'page' => $page, 'query' => $query,
                        'page_hash' => sha1($page), 'query_hash' => sha1($query), 'country' => $country, 'device' => $device,
                        'clicks' => (int) $r['clicks'], 'impressions' => (int) $r['impressions'],
                        'ctr' => $r['ctr'], 'position' => $r['position']];
            if (count($batch) === 1000) {
                $this->flush($batch);
                $batch = [];
            }
        }
        $this->flush($batch);
    }

    private function flush(array $batch): void
    {
        if ($batch) {
            DB::table('gsc_page_query')->upsert($batch,
                ['integration_id', 'date', 'page_hash', 'query_hash', 'country', 'device'],
                ['clicks', 'impressions', 'ctr', 'position']);
        }
    }
}
