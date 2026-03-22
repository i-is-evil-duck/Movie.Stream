import { defineConfig } from 'astro/config';

export default defineConfig({
  // Add this to disable the dev toolbar permanently
  devToolbar: {
    enabled: false
  },
  vite: {
    server: {
      proxy: {
        '/api': 'http://127.0.0.1:8973',
        '/status': 'http://127.0.0.1:8973',
        '/watch': 'http://127.0.0.1:8973'
      }
    }
  }
});