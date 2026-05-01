import "./globals.css";
import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";

export const metadata: Metadata = {
  title: "LGPDoc",
  description: "Anonimização e revisão de documentos com PII",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR">
      <body>
        <AppHeader />
        <main>{children}</main>
        <footer className="app-footer">
          Desenvolvido por Alexandre A. Pires.
        </footer>
      </body>
    </html>
  );
}
