import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import { webcrypto } from 'node:crypto'
import ts from 'typescript'

// Transport tests cover API requests, not the page's route callbacks.
// Reuse the existing TypeScript VM pattern to execute those callbacks with
// isolated hook state; browser layout and live data remain integration checks.
const require = createRequire(import.meta.url)
const source = await readFile(new URL('./DesktopPortalPage.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
})

function page(query, role = 'founder', { evolution = true } = {}) {
  let params = new URLSearchParams(query)
  let index = 0
  // Slot 1 is the Ark agent-evolution capability (useState after the portal data).
  const state = [undefined, evolution]
  delete state[0]
  const hooks = {
    useState: initial => {
      const slot = index++
      if (!(slot in state)) state[slot] = typeof initial === 'function' ? initial() : initial
      return [state[slot], next => { state[slot] = typeof next === 'function' ? next(state[slot]) : next }]
    },
    useRef: value => ({ current: value }),
    useMemo: fn => fn(),
    useCallback: fn => fn,
    useEffect: () => {},
  }
  const empty = { status: 'empty', data: null, error: null }
  const exports = {}
  vm.runInNewContext(outputText, {
    exports, URLSearchParams, crypto:webcrypto,
    require: name => {
      if (name === 'react') return hooks
      if (name === 'react/jsx-runtime') return require(name)
      if (name === 'react-router-dom') return { useSearchParams: () => [params, next => { params = new URLSearchParams(typeof next === 'function' ? next(params) : next) }] }
      if (name.endsWith('/desktop-portal-api')) return {
        createDesktopPortalApi: () => ({execute:async intent=>({kind:intent.kind,result:{response:'Fixture agent response',components:[],tool_calls:[]}})}),
        initialDesktopPortalData: { user: { status: 'ready', data: { id: 'test-founder', name: 'Test account', role } }, artist: empty, bookings: empty, inbox: empty, club: empty, quests: empty },
      }
      const component = name.split('/').at(-1)
      return { default: component, [component]: component }
    },
  })
  return { render: () => { index = 0; return exports.default() }, query: () => params }
}

function find(element, type) {
  if (!element || typeof element !== 'object') return undefined
  if (element.type === type) return element
  if (typeof element.props?.children === 'function') return find(element.props.children({ items: [], total: 0 }), type)
  for (const child of [element.props?.children].flat(Infinity)) {
    const match = find(child, type)
    if (match) return match
  }
}

test('legacy training links open the matching Bench view and contextual cards', () => {
  for (const view of ['tune', 'teach', 'test', 'review']) {
    const shell = page(`portal=pit&view=${view}`).render()
    const workspace = find(shell, 'FounderHubWorkspace')
    assert.equal(workspace.props.workflow, 'train')
    assert.equal(workspace.props.benchView, view)
    assert.deepEqual(Array.from(shell.props.actions, action => action.id), ['tune', 'teach', 'test', 'review'])
    assert.equal(typeof shell.props.onCloseWorkspace, 'function')
  }
})

test('conversation card creates independent agent threads and reopens the selected thread',()=>{
  const app=page('portal=pit&workflow=conversations')
  const list=agent=>find(app.render(),'FounderHubWorkspace').props.renderConversations(agent)
  find(list('booky'),'button').props.onClick()
  assert.equal(app.render().props.agentId,'booky')
  const first=find(list('booky'),'li').props.children
  assert.equal(first.props['aria-pressed'],true)
  find(list('booky'),'button').props.onClick()
  assert.equal(find(list('booky'),'li').props.children.props['aria-pressed'],false)
  first.props.onClick()
  assert.equal(find(list('booky'),'li').props.children.props['aria-pressed'],true)
  assert.equal(find(list('bella'),'li'),undefined)
  find(list('bella'),'button').props.onClick()
  assert.equal(app.render().props.agentId,'bella')
  first.props.onClick()
  assert.equal(app.render().props.agentId,'booky')
  find(list('docbot'),'button').props.onClick()
  assert.equal(app.render().props.agentId,'docbot')
  assert.equal(app.render().props.onSend,undefined)
})

test('archive restoration keeps text turns and excludes Courier and untrusted card payloads',()=>{
  const exports={}
  vm.runInNewContext(outputText,{exports,require:()=>({})})
  const restored=exports.restoreAgentChats(JSON.stringify({
    'booky:chat:one':[{id:'p',from:'me',text:'Estimate please',components:[{type:'action'}]},{id:'r',from:'agent',text:'Which size?'}],
    'mercury:customer-record':[{id:'private',from:'me',text:'Customer context'}],
    bella:[{from:'bad',text:'Invalid'}],
  }))
  assert.equal(exports.agentThreads(restored,'booky')[0].title,'Estimate please')
  assert.equal(restored['booky:chat:one'][0].components,undefined)
  assert.equal(restored['mercury:customer-record'],undefined)
  assert.equal(restored.bella.length,0)
  assert.throws(()=>exports.restoreAgentChats('{broken'))
})

test('reopening a thread restores its prompt and response, not the newest thread',async()=>{
  const app=page('portal=pit&workflow=conversations')
  const list=()=>find(app.render(),'FounderHubWorkspace').props.renderConversations('booky')
  find(list(),'button').props.onClick()
  app.render().props.onSend('Fixture booking question')
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(app.render().props.messages.length,2)
  const saved=find(list(),'li').props.children
  find(list(),'button').props.onClick()
  assert.equal(app.render().props.messages.length,0)
  saved.props.onClick()
  assert.equal(app.render().props.messages[0].text,'Fixture booking question')
  assert.equal(app.render().props.messages[1].text,'Fixture agent response')
})

test('a standalone install keeps Bench and trials out of the founder deck', () => {
  const shell = page('portal=pit', 'founder', { evolution: false }).render()
  const ids = Array.from(shell.props.actions, action => action.id)
  assert.ok(!ids.includes('train') && !ids.includes('runs'))
  assert.ok(ids.includes('roster') && ids.includes('models'))
})

test('closing training clears its view and returns to the unclosable Hub overview', () => {
  const app = page('portal=pit&workflow=train&view=teach&mode=desktop')
  app.render().props.onCloseWorkspace()
  assert.equal(app.query().get('portal'), 'pit')
  assert.equal(app.query().get('workflow'), null)
  assert.equal(app.query().get('view'), null)
  assert.equal(app.query().get('mode'), 'desktop')
  assert.equal(app.render().props.onCloseWorkspace, undefined)
})

test('leaving and returning to Hub does not revive a previous training view', () => {
  const app = page('portal=pit&workflow=train&view=teach')
  app.render().props.onSelectTab('home')
  assert.equal(app.query().get('view'), null)
  app.render().props.onSelectTab('pit')
  assert.equal(find(app.render(), 'FounderHubWorkspace').props.workflow, null)
})

test('explicit workflows and panel links take priority over legacy training views', () => {
  for (const key of ['workflow', 'panel']) {
    const shell = page(`portal=pit&${key}=models&view=teach`).render()
    assert.equal(find(shell, 'FounderHubWorkspace').props.workflow, 'models')
    assert.ok(shell.props.actions.some(action => action.id === 'models'))
  }
  assert.equal(page('portal=pit&workflow=unknown&view=teach').render().props.onCloseWorkspace, undefined)
  assert.equal(page('portal=pit&view=unknown').render().props.onCloseWorkspace, undefined)
})

test('training card callbacks retain Founder workspace identity and select only its view', () => {
  const app = page('portal=pit&workflow=train')
  const initial = find(app.render(), 'FounderHubWorkspace')
  for (const id of ['teach', 'test', 'review', 'tune']) {
    app.render().props.actions.find(action => action.id === id).run()
    const next = find(app.render(), 'FounderHubWorkspace')
    assert.equal(next.type, initial.type)
    assert.equal(next.key, initial.key)
    assert.equal(next.props.workflow, 'train')
    assert.equal(next.props.benchView, id)
  }
})

test('founder cards open explicit workflows and non-founders cannot enter the board', () => {
  const app = page('portal=pit')
  for (const action of app.render().props.actions) {
    action.run()
    assert.equal(app.query().get('workflow'), action.id)
    assert.equal(typeof app.render().props.onCloseWorkspace, 'function')
    app.render().props.onCloseWorkspace()
  }
  for (const role of ['admin', 'artist', 'assistant', 'collector']) {
    assert.equal(find(page('portal=pit&view=teach', role).render(), 'FounderHubWorkspace'), undefined)
  }
})

test('Hub record actions open the selected conversation or booking and clear Hub workflow state', () => {
  const app = page('portal=pit&workflow=brief&view=teach')
  find(app.render(), 'FounderHubWorkspace').props.onOpenConversation('thread-example')
  assert.equal(app.query().get('portal'), 'courier')
  assert.equal(app.query().get('convo'), 'thread-example')
  assert.equal(app.query().get('workflow'), null)
  assert.equal(app.query().get('view'), null)
  app.render().props.onSelectTab('pit')
  find(app.render(), 'FounderHubWorkspace').props.onOpenBooking('booking-example')
  assert.equal(app.query().get('portal'), 'books')
  assert.equal(app.query().get('convo'), null)
  assert.equal(find(app.render(), 'BookyWorkspace').props.selectedId, 'booking-example')
})

test('every signed-in role reaches Hub Connections, collector included; deposits stay artist-only', () => {
  for (const role of ['founder', 'admin', 'artist', 'assistant', 'collector']) {
    const app = page('portal=hub', role)
    const ids = app.render().props.actions.map(action => action.id)
    assert.ok(ids.includes('connections'), role)
    assert.equal(ids.includes('deposits'), role === 'artist', role)
    app.render().props.actions.find(action => action.id === 'connections').run()
    assert.equal(app.query().get('portal'), 'hub')
    assert.equal(app.query().get('workflow'), 'connections')
    assert.ok(find(app.render(), 'ArtistConnections'), role)
  }
  const collector = page('portal=hub', 'collector')
  assert.equal(collector.render().props.actions.map(action => action.id).join(','), 'connections,conversations')
  assert.equal(find(page('portal=hub&workflow=deposits', 'collector').render(), 'ArtistDepositActivity'), undefined)
})
