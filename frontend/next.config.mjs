/**
 * Minimal App Router config. NEXT_PUBLIC_API_BASE_URL (see .env.local.example)
 * carries the backend origin instead of a Next.js rewrite/proxy, so this file
 * has nothing to add yet -- kept explicit rather than omitted so future
 * options (headers, images, etc.) have an obvious place to land.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
