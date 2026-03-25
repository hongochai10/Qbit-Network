import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: 'http://localhost:8545/api/v1/:path*',
      },
      {
        source: '/ws',
        destination: 'http://localhost:8545/ws',
      },
    ];
  },
};

export default nextConfig;
