import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  transpilePackages: ['@mantine/core', '@mantine/hooks', '@mantine/notifications'],
};

export default nextConfig;
