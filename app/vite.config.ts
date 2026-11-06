import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { execSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as path from 'node:path'

// Resolve the /api proxy target. Precedence:
//   1. A real process.env override (e.g. set inline for one session)
//   2. VITE_API_TARGET from the loaded .env file (authoritative across restarts)
//   3. --mode prod-proxy shorthand
//   4. local API default
// loadEnv() is required because vite.config runs in Node before Vite injects
// import.meta.env, so a .env entry is otherwise never seen here.
function resolveApiTarget(mode: string, fileEnv: Record<string, string>): string {
  if (process.env.VITE_API_TARGET) return process.env.VITE_API_TARGET
  if (fileEnv.VITE_API_TARGET) return fileEnv.VITE_API_TARGET
  if (mode === 'prod-proxy') return 'https://ryozan-production.up.railway.app'
  return 'http://127.0.0.1:18810'
}

/* ── GDT Dev API Plugin ─────────────────────────────────────────
 *  Adds dev-only server endpoints for the Galaxy Design Tools:
 *    POST /dev-api/write-tokens  — saves edited CSS token values to tokens.css
 *    POST /dev-api/publish       — git commit + push to trigger Railway deploy
 *  Only active in dev mode (apply: 'serve').
 * ──────────────────────────────────────────────────────────────── */
function gdtDevPlugin(): Plugin {
  const tokensPath = path.resolve(__dirname, 'src/styles/tokens.css')

  return {
    name: 'gdt-dev-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/dev-api', (req, res, next) => {
        if (req.method !== 'POST') { next(); return }
        const url = req.url || ''
        if (url !== '/write-tokens' && url !== '/publish') { next(); return }

        let body = ''
        req.on('data', (chunk: Buffer) => { body += chunk.toString() })
        req.on('end', () => {
          const json = (status: number, data: object) => {
            res.writeHead(status, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' })
            res.end(JSON.stringify(data))
          }

          try {
            const data = JSON.parse(body || '{}')

            if (url === '/write-tokens') {
              const tokens = data.tokens as Record<string, string>
              if (!tokens || typeof tokens !== 'object') { json(400, { ok: false, error: 'Missing tokens' }); return }

              let css = fs.readFileSync(tokensPath, 'utf-8')
              let count = 0
              for (const [name, value] of Object.entries(tokens)) {
                const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
                css = css.replace(
                  new RegExp(`(${escaped}\\s*:\\s*)([^;\\n]+)(;)`, 'g'),
                  `$1${value}$3`
                )
                count++
              }
              fs.writeFileSync(tokensPath, css, 'utf-8')
              json(200, { ok: true, updated: count })

            } else if (url === '/publish') {
              const message = typeof data.message === 'string' && data.message.trim()
                ? data.message.trim()
                : 'style: GDT token edit'
              const repoRoot = path.resolve(__dirname, '..')
              // Stage + commit + push. If nothing to commit, just push.
              try {
                execSync(`git add -A`, { cwd: repoRoot, stdio: 'pipe' })
                execSync(`git commit -m "${message.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/`/g, '\\`').replace(/\$/g, '\\$')}"`, { cwd: repoRoot, stdio: 'pipe' })
              } catch {
                // Nothing new to commit — still attempt push
              }
              execSync('git push', { cwd: repoRoot, stdio: 'pipe' })
              json(200, { ok: true })
            }
          } catch (err) {
            json(500, { ok: false, error: String(err) })
          }
        })
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, __dirname, '')
  const apiTarget = resolveApiTarget(mode, fileEnv)

  // Honor an externally-assigned port (e.g. the preview harness sets PORT).
  // strictPort makes Vite bind exactly there instead of auto-incrementing,
  // so the proxy and the dev server never disagree on which port to use.
  const assignedPort = process.env.PORT ? Number(process.env.PORT) : undefined

  // Milliseconds between polls of each watched path; the compose override owns
  // the value (CHOKIDAR_INTERVAL). Never below 1 s on a Plan 9 share.
  const pollIntervalMs = Math.max(1000, Number(process.env.CHOKIDAR_INTERVAL) || 1000)

  return {
    resolve: {
      alias: [
        { find: '@', replacement: path.resolve(__dirname, './src') },
        // The shared UI package is linked from outside app/. Vercel installs
        // dependencies inside app/, so pin React's runtime to that install
        // instead of asking Rollup to resolve it from packages/ui/.
        { find: /^react$/, replacement: path.resolve(__dirname, 'node_modules/react/index.js') },
        { find: /^react\/jsx-runtime$/, replacement: path.resolve(__dirname, 'node_modules/react/jsx-runtime.js') },
        { find: /^react\/jsx-dev-runtime$/, replacement: path.resolve(__dirname, 'node_modules/react/jsx-dev-runtime.js') },
      ],
    },
    plugins: [
      react(),
      tailwindcss(),
      gdtDevPlugin(),
    ],
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? 'dev'),
    },
    build: {
      rollupOptions: {
        input: {
          main: path.resolve(__dirname, 'index.html'),
        },
      },
    },
    server: {
      // Loopback by default; the compose app service passes --host 0.0.0.0 inside its container.
      host: process.env.VITE_HOST || '127.0.0.1',
      // Windows Docker bind mounts need polling (CHOKIDAR_USEPOLLING from the
      // compose override) and each polled path is a Plan 9 round trip to the
      // host every interval. chokidar's own CHOKIDAR_INTERVAL override only
      // covers text files, so the compose-owned interval is applied to binaries
      // here too, and generated Vercel output, design references, docs and the
      // static assets under public/ are left out of the watch. Edits under
      // public/ need a manual browser reload in the Docker dev stack.
      watch: {
        ignored: ['**/.vercel/**', '**/public/**', '**/design-reference/**', '**/docs/**'],
        interval: pollIntervalMs,
        binaryInterval: Math.max(1500, pollIntervalMs),
      },
      fs: {
        allow: [__dirname, path.resolve(__dirname, '../packages/ui')],
      },
      // Ryozan's local web port is 15310 (API 18810); PORT still overrides it.
      port: assignedPort ?? 15310,
      strictPort: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/docs': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/redoc': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
