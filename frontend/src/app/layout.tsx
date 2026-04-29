import type { Metadata } from "next";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "ProProctor Investigator",
  description: "AI-powered observability agent for ProProctor session analysis",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full flex flex-col" style={{ margin: 0 }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
