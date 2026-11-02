#!/usr/bin/env node
/**
 * New artifact justification:
 * Ryozan already had deploy docs, but not a single command that answers whether
 * a fix is local only, pushed, merged, or actually live. This script stays
 * narrow: it compares local git state with the live commit exposed by
 * /health/deploy so UI review handoffs stop relying on guesswork.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')
// RYOZAN_LIVE_DEPLOY_URL replaced BTTCO_LIVE_DEPLOY_URL; the old name still works with a warning.
const legacyLiveUrl = process.env.BTTCO_LIVE_DEPLOY_URL
if (process.env.RYOZAN_LIVE_DEPLOY_URL === undefined && legacyLiveUrl !== undefined) {
  console.warn('BTTCO_LIVE_DEPLOY_URL is deprecated; set RYOZAN_LIVE_DEPLOY_URL instead.')
}
const liveUrl = process.env.RYOZAN_LIVE_DEPLOY_URL ?? legacyLiveUrl ?? 'https://ryozan-production.up.railway.app/health/deploy'
const wantJson = process.argv.includes('--json')

function git(args, allowFailure = false) {
  try {
    return execFileSync('git', args, {
      cwd: repoRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch (error) {
    if (allowFailure) return null
    throw error
  }
}

function shortSha(value) {
  return value ? value.slice(0, 7) : null
}

function fetchOriginMain() {
  try {
    execFileSync('git', ['fetch', 'origin', 'main', '--quiet'], {
      cwd: repoRoot,
      stdio: ['ignore', 'ignore', 'ignore'],
    })
    return true
  } catch {
    return false
  }
}

function isAncestor(base, target) {
  try {
    execFileSync('git', ['merge-base', '--is-ancestor', base, target], {
      cwd: repoRoot,
      stdio: ['ignore', 'ignore', 'ignore'],
    })
    return true
  } catch {
    return false
  }
}

async function fetchLiveDeployState() {
  try {
    const response = await fetch(liveUrl, {
      headers: { accept: 'application/json' },
    })
    const body = await response.text()
    let parsed
    try {
      parsed = JSON.parse(body)
    } catch {
      parsed = { raw: body }
    }
    const deploy = parsed?.deploy && typeof parsed.deploy === 'object' ? parsed.deploy : parsed
    return {
      ok: response.ok,
      status: response.status,
      url: liveUrl,
      deploy,
    }
  } catch (error) {
    return {
      ok: false,
      status: null,
      url: liveUrl,
      error: error instanceof Error ? error.message : String(error),
      deploy: null,
    }
  }
}

function buildNextAction(summary) {
  if (!summary.pushed) {
    return `Push ${summary.branch} so the branch commit is visible outside your machine.`
  }
  if (!summary.mergedIntoMain) {
    return 'Open or finish the PR into main. Railway only deploys what reaches main.'
  }
  if (summary.liveMatchesHead) {
    return 'This commit is live. Share the production URL for review.'
  }
  if (summary.liveMatchesOriginMain) {
    return 'Main is live, but this branch is not. Merge the PR or switch review to a local preview.'
  }
  if (summary.liveCommit) {
    return `Live is ${shortSha(summary.liveCommit)}. Wait for Railway or inspect the production deployment before re-testing.`
  }
  return `Live commit is unknown. Check ${liveUrl} after the backend deploy metadata endpoint is reachable.`
}

async function main() {
  const fetchedOriginMain = fetchOriginMain()
  const branch = git(['rev-parse', '--abbrev-ref', 'HEAD'])
  const head = git(['rev-parse', 'HEAD'])
  const upstream = git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], true)
  const upstreamHead = upstream ? git(['rev-parse', upstream], true) : null
  const originMain = git(['rev-parse', 'origin/main'])
  const headTree = git(['show', '--no-patch', '--format=%T', 'HEAD'])
  const originMainTree = git(['show', '--no-patch', '--format=%T', 'origin/main'])
  const mergedIntoMain = isAncestor('HEAD', 'origin/main') || headTree === originMainTree
  const pushed = upstreamHead ? upstreamHead === head : false

  const live = await fetchLiveDeployState()
  const liveCommit = live.deploy?.commit ?? null
  const liveBranch = live.deploy?.branch ?? null
  const liveMatchesHead = Boolean(liveCommit && liveCommit === head)
  const liveMatchesOriginMain = Boolean(liveCommit && liveCommit === originMain)

  const summary = {
    branch,
    head,
    headShort: shortSha(head),
    upstream,
    upstreamHead,
    upstreamHeadShort: shortSha(upstreamHead),
    originMain,
    originMainShort: shortSha(originMain),
    fetchedOriginMain,
    pushed,
    mergedIntoMain,
    liveUrl: live.url,
    liveStatus: live.status,
    liveCommit,
    liveCommitShort: shortSha(liveCommit),
    liveBranch,
    liveEnvironment: live.deploy?.environment ?? null,
    livePlatform: live.deploy?.platform ?? null,
    liveMatchesHead,
    liveMatchesOriginMain,
    liveError: live.error ?? null,
  }

  const result = {
    summary,
    checklist: {
      local: true,
      pushed,
      merged: mergedIntoMain,
      live: liveMatchesHead,
      productionSourceIsOriginMain: liveMatchesOriginMain,
    },
    nextAction: buildNextAction(summary),
  }

  if (wantJson) {
    console.log(JSON.stringify(result, null, 2))
    return
  }

  console.log(`Branch:        ${branch}`)
  console.log(`HEAD:          ${shortSha(head)} (${head})`)
  console.log(`Upstream:      ${upstream ?? '(none)'}`)
  console.log(`Upstream HEAD: ${upstreamHead ? `${shortSha(upstreamHead)} (${upstreamHead})` : '(unknown)'}`)
  console.log(`origin/main:   ${shortSha(originMain)} (${originMain})`)
  console.log(`Pushed:        ${pushed ? 'yes' : 'no'}`)
  console.log(`Merged:        ${mergedIntoMain ? 'yes' : 'no'}`)
  console.log(`Live URL:      ${live.url}`)
  console.log(`Live status:   ${live.status ?? 'unreachable'}`)
  console.log(`Live branch:   ${liveBranch ?? '(unknown)'}`)
  console.log(`Live commit:   ${liveCommit ? `${shortSha(liveCommit)} (${liveCommit})` : '(unknown)'}`)
  console.log(`HEAD live:     ${liveMatchesHead ? 'yes' : 'no'}`)
  console.log(`main live:     ${liveMatchesOriginMain ? 'yes' : 'no'}`)
  if (summary.liveError) {
    console.log(`Live error:    ${summary.liveError}`)
  }
  if (!fetchedOriginMain) {
    console.log('Fetch note:    Could not refresh origin/main before comparing.')
  }
  console.log(`Next action:   ${result.nextAction}`)
}

await main()
