<?php

namespace App\Services\Seo;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;

/**
 * IndexNow (Bing, Yandex, Naver, Seznam — not Google).
 * 1. Generate a key once per host: bin2hex(random_bytes(16)); store it (e.g. settings / tenant domain row).
 * 2. Serve it:  Route::get('/{key}.txt', fn ($key) => $key === IndexNow::keyFor(request()->getHost())
 *                   ? response($key, 200, ['Content-Type' => 'text/plain']) : abort(404))->where('key', '[a-f0-9]{32}');
 * 3. Call IndexNow::queue($url) when a public page is published, updated or deleted (model observer).
 * 4. Schedule::call(fn () => app(IndexNow::class)->flush())->everyFiveMinutes();
 */
class IndexNow
{
    public static function keyFor(string $host): ?string
    {
        return cache()->remember("indexnow_key:$host", 3600, fn () => DB::table('domains')->where('host', $host)->value('indexnow_key'));
    }

    public static function queue(string $url): void
    {
        $host = parse_url($url, PHP_URL_HOST);
        if ($host && app()->isProduction()) {
            DB::table('indexnow_queue')->insert(['host' => $host, 'url' => $url, 'queued_at' => now()]);
        }
    }

    public function flush(): void
    {
        DB::table('indexnow_queue')->whereNull('sent_at')->select('host')->distinct()->pluck('host')->each(function ($host) {
            $key = self::keyFor($host);
            if (! $key) {
                return;
            }
            $pending = DB::table('indexnow_queue')->where('host', $host)->whereNull('sent_at')->limit(10000)->get(['id', 'url']);
            $urls = $pending->pluck('url')->unique()->values()->all();
            $res = Http::timeout(20)->post('https://api.indexnow.org/indexnow', [
                'host' => $host, 'key' => $key, 'keyLocation' => "https://{$host}/{$key}.txt", 'urlList' => $urls,
            ]);
            // 200/202 ok · 400 bad request · 403 key not found · 422 URLs not on host · 429 too many requests
            if ($res->status() !== 429) {
                DB::table('indexnow_queue')->whereIn('id', $pending->pluck('id'))
                    ->update(['sent_at' => now(), 'response_code' => $res->status()]);
            }
        });
    }
}
