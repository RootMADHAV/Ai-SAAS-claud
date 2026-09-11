import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { NavBar } from "@/components/nav-bar";
import { AuthProvider } from "@/lib/auth/context";

export const metadata: Metadata = {
  title: "Security Platform",
  description: "AI-assisted vulnerability discovery and reporting",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AuthProvider>
          <NavBar />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
