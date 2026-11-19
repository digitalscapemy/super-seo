import type { Metadata } from "next";

export const metadata: Metadata = {
  metadataBase: new URL("https://acme.test"),
  title: { default: "Acme Invoicing", template: "%s | Acme" },
  description: "Invoicing for freelancers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
