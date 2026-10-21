import type { Metadata, Viewport } from "next";
import {
  Inter,
  JetBrains_Mono,
  Noto_Sans_SC,
  Plus_Jakarta_Sans,
} from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  variable: "--font-plus-jakarta-sans",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

const notoSansSC = Noto_Sans_SC({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-noto-sans-sc",
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: "Enforge — Deep Research Writing Agent",
  description: "Deep research writing agent infrastructure status.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fcfbfa" },
    { media: "(prefers-color-scheme: dark)", color: "#101010" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${plusJakartaSans.variable} ${jetbrainsMono.variable} ${notoSansSC.variable} min-h-screen antialiased`}
      >
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
