"""Offline tests for extensions/shopify/shopify.py (fake transport, no network, no real token)."""
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "extensions" / "shopify"))
import shopify  # noqa: E402


def gql(data, available=1000.0, requested=100, actual=50, restore=50.0, errors=None):
    body = {"data": data, "extensions": {"cost": {"requestedQueryCost": requested, "actualQueryCost": actual,
            "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": available, "restoreRate": restore}}}}
    if errors:
        body["errors"] = errors
    return 200, {"x-shopify-api-version": "2025-10"}, json.dumps(body).encode()


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        return self.responses.pop(0)


def client(responses, sleeps=None):
    t = Transport(responses)
    s = sleeps if sleeps is not None else []
    c = shopify.ShopifyClient("myshop.myshopify.com", "shpat_secret", "2025-10", transport=t, sleep=s.append)
    return c, t, s


def product(handle, title, status="ACTIVE", desc="word " * 80, seo_t="SEO title", seo_d="SEO description", media=None,
            url="https://shop.com/products/x"):
    return {"id": f"gid://shopify/Product/{handle}", "title": title, "handle": handle, "status": status,
            "onlineStoreUrl": url, "description": desc, "seo": {"title": seo_t, "description": seo_d},
            "media": {"nodes": media if media is not None else [{"mediaContentType": "IMAGE", "alt": "Front view"}]}}


def page(root, nodes, has_next=False, cursor=None):
    return {root: {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}


class Client(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(shopify.ShopifyError):
            shopify.ShopifyClient("evil.com", "t")
        with self.assertRaises(shopify.ShopifyError):
            shopify.ShopifyClient("myshop.myshopify.com", "")
        with self.assertRaises(shopify.ShopifyError):
            shopify.ShopifyClient("myshop.myshopify.com", "t", "2025-13")
        c = shopify.ShopifyClient("https://MyShop.myshopify.com/", "shpat_secret")
        self.assertEqual(c.url, f"https://myshop.myshopify.com/admin/api/{shopify.DEFAULT_VERSION}/graphql.json")
        self.assertNotIn("shpat_secret", repr(c))

    def test_pagination_and_token_header(self):
        c, t, _ = client([gql(page("products", [product("a", "A")], True, "c1")),
                          gql(page("products", [product("b", "B")], False))])
        nodes = list(c.paginate(shopify.PRODUCTS_Q, "products", 50, 100))
        self.assertEqual([n["handle"] for n in nodes], ["a", "b"])
        self.assertIsNone(t.calls[0]["body"]["variables"]["after"])
        self.assertEqual(t.calls[1]["body"]["variables"]["after"], "c1")
        self.assertEqual(t.calls[0]["headers"]["X-Shopify-Access-Token"], "shpat_secret")

    def test_limit_stops_pagination(self):
        c, t, _ = client([gql(page("products", [product("a", "A"), product("b", "B")], True, "c1"))])
        self.assertEqual(len(list(c.paginate(shopify.PRODUCTS_Q, "products", 50, 1))), 1)
        self.assertEqual(len(t.calls), 1)

    def test_preemptive_throttle_sleep(self):
        c, _, s = client([gql(page("products", [], True, "c1"), available=20, requested=120, restore=50),
                          gql(page("products", [], False))])
        list(c.paginate(shopify.PRODUCTS_Q, "products", 50, 100))
        self.assertEqual(len(s), 1)
        self.assertAlmostEqual(s[0], 2.0)  # (120 - 20) / 50

    def test_throttled_error_retry(self):
        throttled = gql(None, available=10, requested=110, restore=50,
                        errors=[{"message": "Throttled", "extensions": {"code": "THROTTLED"}}])
        c, t, s = client([throttled, gql(page("products", [], False))])
        list(c.paginate(shopify.PRODUCTS_Q, "products", 50, 100))
        self.assertEqual(len(t.calls), 2)
        self.assertAlmostEqual(s[0], 2.0)

    def test_429_retry_after_and_5xx_backoff(self):
        c, t, s = client([(429, {"retry-after": "3"}, b""), (503, {}, b""), gql(page("products", [], False))])
        list(c.paginate(shopify.PRODUCTS_Q, "products", 50, 100))
        self.assertEqual(s[0], 3.0)
        self.assertGreaterEqual(s[1], 2.0)
        self.assertEqual(len(t.calls), 3)

    def test_auth_error(self):
        c, _, _ = client([(401, {}, b"")])
        with self.assertRaises(shopify.ShopifyError) as cm:
            c.graphql("query{shop{name}}")
        self.assertNotIn("shpat_secret", str(cm.exception))

    def test_graphql_error_raises(self):
        c, _, _ = client([gql(None, errors=[{"message": "Access denied for urlRedirects field."}])])
        with self.assertRaises(shopify.ShopifyError):
            c.graphql(shopify.REDIRECTS_Q)


class Checks(unittest.TestCase):
    def codes(self, res):
        return {f["code"]: f for f in res["findings"]}

    def test_products(self):
        nodes = [
            product("good", "Linen Shirt Blue"),
            product("dup1", "Cotton Tee", seo_t="", seo_d=""),
            product("dup2", "cotton  tee"),
            product("noimg", "Hat", media=[]),
            product("noalt", "Scarf", media=[{"mediaContentType": "IMAGE", "alt": ""}, {"mediaContentType": "VIDEO", "alt": ""}]),
            product("empty", "Sock", desc=""),
            product("short", "Belt", desc="Nice leather belt."),
            product("draft", "Draft Item", status="DRAFT", desc="", media=[]),
            product("hidden", "Hidden", url=None),
        ]
        r = shopify.check_products(nodes)
        c = self.codes(r)
        self.assertEqual(r["active"], 8)
        self.assertEqual(r["by_status"]["DRAFT"], 1)
        self.assertEqual(c["product_duplicate_titles"]["count"], 1)
        self.assertEqual(c["product_no_images"]["severity"], "high")
        self.assertEqual(c["product_no_images"]["items"][0]["handle"], "noimg")
        self.assertEqual(c["product_images_missing_alt"]["items"][0]["images_without_alt"], 1)
        self.assertEqual(c["product_empty_description"]["items"][0]["handle"], "empty")
        self.assertEqual(c["product_short_description"]["items"][0]["handle"], "short")
        self.assertIn("product_missing_seo_title", c)
        self.assertEqual(c["product_missing_seo_description"]["severity"], "medium")
        self.assertIn("product_duplicate_seo_titles", c)  # "SEO title" shared
        self.assertIn("product_not_on_online_store", c)
        self.assertNotIn("draft", json.dumps(c["product_empty_description"]["items"]))
        for f in r["findings"]:
            self.assertIn(f["severity"], shopify.SEVERITY_ORDER)

    def test_collections(self):
        nodes = [
            {"title": "Shirts", "handle": "shirts", "description": "word " * 60, "seo": {"title": "Shirts", "description": "d"},
             "image": {"altText": "Shirts on a rail"}, "productsCount": {"count": 12}},
            {"title": "Sale", "handle": "sale", "description": "", "seo": {"title": None, "description": None},
             "image": None, "productsCount": {"count": 0}},
            {"title": "Hats", "handle": "hats", "description": "Hats.", "seo": {"title": "", "description": ""},
             "image": {"altText": ""}, "productsCount": {"count": 3}},
        ]
        c = self.codes(shopify.check_collections(nodes))
        self.assertEqual(c["collection_empty"]["items"][0]["handle"], "sale")
        self.assertIn("collection_empty_description", c)
        self.assertIn("collection_short_description", c)
        self.assertIn("collection_no_image", c)
        self.assertIn("collection_image_missing_alt", c)

    def test_redirects(self):
        nodes = [
            {"path": "/a", "target": "/b"}, {"path": "/b", "target": "/c"}, {"path": "/c", "target": "/products/x"},
            {"path": "/loop1", "target": "/loop2"}, {"path": "/loop2/", "target": "/LOOP1"},
            {"path": "/self", "target": "https://myshop.myshopify.com/self"},
            {"path": "/out", "target": "https://other.com/page"},
            {"path": "/ok", "target": "/products/y"},
        ]
        c = self.codes(shopify.check_redirects(nodes, {"myshop.myshopify.com"}))
        self.assertEqual(c["redirect_loop"]["count"], 1)
        self.assertEqual(c["redirect_self"]["items"][0]["path"], "/self")
        chains = {i["path"]: i for i in c["redirect_chain"]["items"]}
        self.assertEqual(chains["/a"]["hops"], 3)
        self.assertEqual(chains["/a"]["final"], "/products/x")
        self.assertNotIn("/ok", chains)
        self.assertEqual(c["redirect_external"]["severity"], "info")


class Audit(unittest.TestCase):
    def test_audit_runs_all_sections(self):
        responses = [
            gql(page("products", [product("a", "A", media=[])], False)),
            gql(page("collections", [], False)),
            gql({"shop": {"name": "S", "myshopifyDomain": "myshop.myshopify.com", "primaryDomain": {"host": "www.shop.com", "url": "https://www.shop.com"}}}),
            gql(page("urlRedirects", [{"path": "/x", "target": "https://shop.com/x"}], False)),
        ]
        c, t, _ = client(responses)
        args = types.SimpleNamespace(limit=100, short_words=50)
        r = shopify.run("audit", c, args)
        self.assertEqual(set(r["sections"]), {"products", "collections", "redirects"})
        self.assertIn("product_no_images", {f["code"] for f in r["findings"]})
        self.assertIn("redirect_self", {f["code"] for f in r["findings"]})
        self.assertEqual(r["summary"]["totals"]["products"], 1)
        self.assertNotIn("shpat_secret", json.dumps(r))
        self.assertIn("# Shopify SEO audit", shopify.to_markdown(r))

    def test_section_error_becomes_finding(self):
        c, _, _ = client([gql(None, errors=[{"message": "Access denied for urlRedirects field."}])] * 2)
        args = types.SimpleNamespace(limit=100, short_words=50)
        r = shopify.run("redirects", c, args)
        self.assertEqual(r["findings"][0]["code"], "redirects_unavailable")

    def test_version_fallback_reported(self):
        body = gql(page("collections", [], False))
        c, _, _ = client([(200, {"x-shopify-api-version": "2026-01"}, body[2])])
        r = shopify.run("collections", c, types.SimpleNamespace(limit=10, short_words=50))
        self.assertIn("api_version_fallback", {f["code"] for f in r["findings"]})

    def test_manifest(self):
        m = json.loads((ROOT / "extensions" / "shopify" / "extension.json").read_text(encoding="utf-8"))
        self.assertEqual(m["name"], "shopify")
        self.assertIn("SHOPIFY_ADMIN_TOKEN", m["requires"]["env_any"])


if __name__ == "__main__":
    unittest.main()
