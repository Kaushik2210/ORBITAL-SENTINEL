import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "uplot/dist/uPlot.min.css";
import "./globals.css";
import { Nav } from "@/components/nav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Orbital Sentinel — Mission Control",
  description:
    "Defensive spacecraft-cybersecurity simulation: telling cyberattacks from failures, faults and space weather.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col pb-10">
        <Nav />
        <main className="mx-4 mt-4 flex-1">{children}</main>
      </body>
    </html>
  );
}
