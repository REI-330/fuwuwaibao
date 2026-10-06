import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3000";
  const forwardedProto = requestHeaders.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const protocol = forwardedProto === "http" || forwardedProto === "https"
    ? forwardedProto
    : process.env.NODE_ENV === "production" && !/^(localhost|127\.0\.0\.1)(:\d+)?$/i.test(host)
      ? "https"
      : "http";
  const previewImage = `${protocol}://${host}/og.png`;

  return {
    title: "向新 · AI职业成长伙伴",
    description:
      "从了解自己开始，通过职业探索、成长路径和行动训练，走出可信的成长路径。",
    icons: { icon: "/og.png", shortcut: "/og.png" },
    openGraph: {
      title: "向新 · AI职业成长伙伴",
      description: "从了解自己开始，走出可信的成长路径",
      images: [
        {
          url: previewImage,
          width: 1200,
          height: 630,
          alt: "向新 AI职业成长伙伴",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "向新 · AI职业成长伙伴",
      description: "从了解自己开始，走出可信的成长路径",
      images: [previewImage],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
