import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ['@mantine/core', '@mantine/hooks', '@mantine/notifications'],
};

export default nextConfig;
