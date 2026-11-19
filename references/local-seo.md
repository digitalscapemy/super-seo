# Local SEO (offices, showrooms, service areas)

For SaaS and online businesses that also have a physical office, showroom, clinic, training centre or a team that
serves customers in specific areas. Pure online SaaS with no customer-facing address needs only `Organization`
markup and a correct "Contact" page; skip the rest.

## Contents
1. Is local SEO relevant? · 2. Google Business Profile · 3. Schema (LocalBusiness / Organization) · 4. Location pages
5. Bing Places and Apple Business Connect · 6. Citations and directories · 7. Service-area businesses · 8. Checklist

## 1. Is local SEO relevant?
| Situation | What to do |
|---|---|
| Online-only SaaS, registered office not open to visitors | `Organization` with `address` on About/Contact; **no** Business Profile (not eligible) |
| Office that customers visit (demos, onboarding, support desk) | Business Profile + `LocalBusiness` subtype on the contact/location page |
| Several offices / cities | One profile and one location page per real staffed location |
| Staff travel to customers (installers, trainers, consultants) | Service-area profile, address hidden (§7) |
Eligibility rule of thumb: in-person contact with customers during stated hours, at a real address with signage.
Virtual offices, co-working desks without staff and PO boxes are not eligible and risk suspension.

## 2. Google Business Profile (GBP)
- **Name**: the real-world business name as on signage, invoices and the website. No keywords or cities stuffed in
  ("Acme Payroll Software Kuala Lumpur Best" violates the guidelines and is a common suspension cause).
- **Categories**: one primary category that describes the business itself (e.g. "Software company"), a few precise
  secondary categories. The primary category is a strong ranking factor; check what top-ranking local competitors use.
- **Services / products**: list real services with short descriptions; add products with price where it helps.
- **NAP consistency**: name, address, phone identical (format included) on GBP, the website footer/contact page,
  schema, and major directories. Use a local phone number, not only a call-centre number.
- **Hours**: regular + special hours (public holidays); "Open 24 hours" only if true.
- **Website link**: the matching location page (not always the home page); add UTM parameters to see GBP traffic in
  analytics (`?utm_source=google&utm_medium=organic&utm_campaign=gbp`).
- **Photos**: real exterior (helps people find the door), interior, team, product in use. No stock photos.
- **Reviews**: ask every real customer, with a direct review link; reply to all, including negative ones, factually.
  Never buy, gate (only asking happy customers), incentivise or post reviews for yourself/competitors; Google removes
  them and can restrict the profile. Don't copy GBP reviews into your site's markup as your own `aggregateRating`.
- **Posts / updates**: optional; useful for offers, events and launches. Low direct ranking impact; keep them current
  if used.
- **Q&A**: Google has been phasing out the public Q&A feature in favour of AI-generated answers from profile content
  and reviews. Check whether it is still shown for the profile; either way, put real FAQs on the location page and
  keep the profile's description, services and attributes complete, since that is what answers are drawn from.
- **Verification**: video, phone, postcard or instant verification depending on category/country. Address changes
  may trigger re-verification; plan moves ahead.
- Track: GBP Performance (calls, direction requests, website clicks, search terms) monthly.

## 3. Schema: LocalBusiness / Organization
- Location page: the most specific `LocalBusiness` subtype that fits (`ProfessionalService`, `Store`,
  `MedicalClinic`, `ComputerStore`...) or `Organization` + `location` (`Place`) when no subtype fits a software company.
- Required in practice: `name`, `address` (`PostalAddress`: `streetAddress`, `addressLocality`, `addressRegion`,
  `postalCode`, `addressCountry` ISO code), `telephone` (E.164, e.g. `+60 3-1234 5678`), `url` (the location page).
- Recommended: `geo` (`GeoCoordinates`, ≥ 5 decimal places), `openingHoursSpecification` (`dayOfWeek`, `opens`,
  `closes`, plus special hours with `validFrom`/`validThrough`), `image`, `priceRange`, `areaServed`, `sameAs` (GBP
  `https://maps.google.com/?cid=...` link, other verified profiles), `parentOrganization` → `#organization` `@id`.
- Example (location page; replace every value with real data, never ship placeholders):
  ```json
  {"@context": "https://schema.org", "@type": "ProfessionalService", "@id": "https://acme.example/locations/penang#place",
   "name": "Acme Payroll", "url": "https://acme.example/locations/penang", "telephone": "+60 4-123 4567",
   "image": "https://acme.example/img/penang-office.jpg",
   "address": {"@type": "PostalAddress", "streetAddress": "Level 5, 1 Jalan Example", "addressLocality": "George Town",
               "addressRegion": "Pulau Pinang", "postalCode": "10050", "addressCountry": "MY"},
   "geo": {"@type": "GeoCoordinates", "latitude": 5.41412, "longitude": 100.32876},
   "openingHoursSpecification": [{"@type": "OpeningHoursSpecification",
     "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "opens": "09:00", "closes": "18:00"}],
   "areaServed": {"@type": "State", "name": "Penang"},
   "parentOrganization": {"@id": "https://acme.example/#organization"}}
  ```
- Multi-location: one LocalBusiness per location page with its own `@id` (`https://domain/locations/penang#place`);
  the home page keeps the `Organization` graph, not a list of every branch.
- Values must match GBP and the visible page exactly. Don't add `aggregateRating` from GBP reviews (self-serving
  LocalBusiness ratings are not displayed and copying third-party ratings is against the guidelines).

## 4. Location pages
A location page exists for a real place or a real service area, and must help someone choose and reach it.
- Contents: H1 with business + place ("Acme Payroll — Penang office"), full NAP, embedded map or directions, hours,
  parking/transport, photos of that office, the team there, services offered at that location, local customer
  stories, local FAQs (e.g. local regulations or language support), a contact form/booking for that office.
- URL: `/locations/<city>` or `/<city>`; linked from a locations hub, the footer or contact page.
- **Uniqueness gates** (same spirit as `saas-pages.md` §4; city-swapped pages are scaled-content abuse):
  | Gate | Threshold |
  |---|---|
  | Unique content per page (excl. nav/footer) | **≥ 60% unique** vs the other location pages |
  | Number of location pages | **warn at 30+**; **stop at 50+** unless each maps to a real staffed location or a documented service area with local proof |
  | Pages for areas you don't serve or have no presence in | do not publish |
  | Human review | every page before publishing; re-check hours/NAP quarterly |
- Service-area "near me" pages without an office need real local substance (jobs done there, local pricing, team
  coverage, local testimonials); otherwise one well-built service page listing the areas is better.

## 5. Bing Places and Apple Business Connect
- **Bing Places for Business**: free; can import directly from Google Business Profile (keeps NAP in sync). Feeds
  Bing Maps, Bing search and products built on Bing's index. Verify, then check categories after import.
- **Apple Business Connect**: free; controls the place card in Apple Maps, Siri, Spotlight and Wallet. Claim the
  location, add hours, photos, categories, action links (booking/website) and "Showcases" for offers.
- Keep all three (Google, Bing, Apple) identical; update them together when hours or phone numbers change.

## 6. Citations and directories
Citations (mentions of the business with NAP) mostly matter for consistency and discovery; quality beats quantity.
| Directory type | Examples (generic types) | Priority |
|---|---|---|
| Map / platform listings | Google, Bing, Apple, OpenStreetMap | Must |
| Data aggregators / navigation apps | Regional navigation apps commonly used in Malaysia/Singapore, car navigation data providers | High where used |
| National business directories | Yellow-pages-style national directories, chamber of commerce member lists | High |
| Government / registry | Company registry profile, government supplier or startup registries, industry-licence registers | High (trust) |
| Industry / software directories | Software review and comparison sites, app marketplaces, integration partner directories | High for SaaS |
| Local associations | Tech/startup associations, city business associations, co-working community pages | Medium |
| Social profiles | LinkedIn company page, Facebook page with address and hours | Medium |
| Bulk/low-quality directories, paid link lists | — | Skip |
Rules: same NAP format everywhere; claim, don't duplicate; remove/merge old listings after a move; record every
listing (URL, login owner, date) in `.seo/project.md` so they can be updated later.

## 7. Service-area businesses (SAB)
- GBP: set service areas (cities/regions, up to ~20), **hide the address** if customers aren't served there. Don't
  list a home address publicly.
- Website: state the areas served on the service page; `LocalBusiness` with `areaServed` (City/AdministrativeArea/
  GeoCircle) and **no** street address in markup if it is hidden on GBP (keep `addressLocality`/`addressCountry`).
- Rankings are strongest near the business's verified location; extra city pages don't change that, real reviews
  and local mentions do.
- Hybrid (office + travel): show the address, also set service areas.

## 8. Checklist
- [ ] Eligible? (staffed address with customer contact) — else Organization only
- [ ] GBP: real name, right primary category, services, hours, photos, website = location page with UTM
- [ ] NAP identical on site footer/contact, schema, GBP, Bing, Apple, top directories
- [ ] Location page per real location: unique (≥ 60%), map, hours, photos, local FAQ, LocalBusiness JSON-LD validated
- [ ] Review process: ask every customer, reply to all, no gating/incentives
- [ ] Bing Places + Apple Business Connect claimed and synced
- [ ] 10–30 quality citations recorded in `.seo/project.md`
- [ ] Monthly: GBP performance, new reviews answered, hours for upcoming holidays
