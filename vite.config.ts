import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const proxy={ '/api': { target: process.env.POWERSHIFT_API_URL || 'http://127.0.0.1:8011', changeOrigin: true, ws: true } };
export default defineConfig({ plugins: [react()], server: { port: 5180, strictPort: true, proxy }, preview:{host:'127.0.0.1',port:5180,strictPort:true,proxy}, build: { rollupOptions: { output: { manualChunks: { map: ['maplibre-gl'], ui: ['react','react-dom','@radix-ui/react-dialog'] } } }, chunkSizeWarningLimit: 1100 } });
