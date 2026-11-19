export const metadata = { title: "Invoicing for freelancers", alternates: { canonical: "/" } };

const jsonLd = { "@context": "https://schema.org", "@type": "Organization", name: "Acme", url: "https://acme.test" };

export default function Home() {
  return (
    <main>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <h1>Invoicing for freelancers</h1>
    </main>
  );
}
