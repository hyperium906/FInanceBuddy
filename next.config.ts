import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    // A bank CSV travels to the import action as text. Exports are small, so
    // this is capped well below anything that could be abused.
    serverActions: { bodySizeLimit: "10mb" },
  },
};

export default nextConfig;
