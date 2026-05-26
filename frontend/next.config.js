/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output → tiny runtime image (server.js + minimal node_modules),
  // important on the small prod box. See frontend/Dockerfile.
  output: 'standalone',
}

module.exports = nextConfig
