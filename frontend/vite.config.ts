import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev the API is proxied; in the container nginx serves the built assets and
// proxies /api itself. VITE_API_BASE_URL is the single switch between the two.
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		port: 5173,
		proxy: {
			"/api": { target: "http://localhost:8000", changeOrigin: true },
		},
	},
	build: {
		outDir: "dist",
		sourcemap: false,
	},
});
