import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

// DesktopPortalPage tests own route selection, not the Model desk's local draft.
// Reuse their isolated hook harness to check assignment controls without writing
// to Ark or substituting fixture data in the running application.
const require = createRequire(import.meta.url)
const source = await readFile(new URL('./FounderHubWorkspace.tsx', import.meta.url), 'utf8')
const registrySource = await readFile(new URL('../../lib/agent-card-hands.ts', import.meta.url), 'utf8')
const registry = {}
vm.runInNewContext(ts.transpileModule(registrySource, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText, {exports:registry})
const { outputText } = ts.transpileModule(`${source}\nexport { ModelRoutingEditor, FounderBoard };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
})

function findAll(element, predicate) {
  if (!element || typeof element !== 'object') return []
  return [ ...(predicate(element) ? [element] : []), ...[element.props?.children].flat(Infinity).flatMap(child => findAll(child, predicate)) ]
}
function text(element) {
  if (typeof element === 'string' || typeof element === 'number') return String(element)
  if (!element || typeof element !== 'object') return ''
  return [element?.props?.children].flat(Infinity).map(text).join('')
}
function button(tree, label) {
  const result = findAll(tree, node => node.type === 'button' && (text(node) === label || node.props['aria-label'] === label))[0]
  assert.ok(result, `Missing button: ${label}`)
  return result
}

async function desk() {
  let slot = 0
  const state = []
  const effects = []
  const dependencies = []
  let poll
  const writes = []
  const models = ['Alpha', 'Beta', 'Gamma'].map(label => ({ id: `ark/${label.toLowerCase()}`, label, provider: 'test', source: 'fixture', assignable: true, assignmentReason: 'Registered route' }))
  let routing = {
    orchestraId: 'orchestra:bttco', scope: 'bttco_chat', models,
    members: [{ actorRef: 'agent:gala', displayName: 'Gala', assignment: { defaultModel: models[0].id, fallbacks: [models[1].id], revision: 3 } }],
    inventory: { notes: [], sections: [{ id: 'fixture', label: 'Fixture provider', authState: 'unknown', warnings: [], models }] },
  }
  const api = {
    getModelRouting: async () => ({ ok: true, routing }),
    saveModelRouting: async request => { writes.push(request); return { ok: true, actorRef: request.actorRef, assignment: { ...request, revision: request.expectedRevision + 1 } } },
  }
  const hooks = {
    useState: initial => {
      const index = slot++
      if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial
      return [state[index], next => { state[index] = typeof next === 'function' ? next(state[index]) : next }]
    },
    useRef: initial => {
      const index = slot++
      return state[index] ??= { current: initial }
    },
    useEffect: (callback, deps) => {
      const index = slot++
      if (!dependencies[index] || deps.some((value, i) => value !== dependencies[index][i])) effects.push(callback)
      dependencies[index] = deps
    },
  }
  const exports = {}
  vm.runInNewContext(outputText, {
    exports,
    setInterval: callback => { poll = callback; return 1 }, clearInterval: () => {},
    require: name => name === 'react' ? hooks : name === 'react/jsx-runtime' ? require(name) : name.endsWith('/api') ? { gala: api } : {},
  })
  const render = () => { slot = 0; const result = exports.ModelRoutingEditor({ agentId: 'gala' }); effects.splice(0).forEach(effect => effect()); return result }
  render()
  await new Promise(resolve => setImmediate(resolve))
  return {
    render, writes,
    advanceRevision: async () => {
      routing = { ...routing, members: routing.members.map(member => ({ ...member, assignment: { ...member.assignment, revision: 4 } })) }
      await poll()
      await new Promise(resolve => setImmediate(resolve))
    },
  }
}

test('editing and discarding preserves the saved assignment without writes', async () => {
  const app = await desk()
  button(app.render(), 'Edit assignment').props.onClick()
  assert.equal(button(app.render(), 'Save shared assignment').props.disabled, true)
  button(app.render(), 'Use account default').props.onClick()
  assert.equal(button(app.render(), 'Save shared assignment').props.disabled, false)
  button(app.render(), 'Discard draft').props.onClick()
  assert.match(text(app.render()), /PrimaryAlpha/)
  assert.match(text(app.render()), /1\. Beta/)
  assert.equal(app.writes.length, 0)
})

test('choosing a fallback as primary removes it from the ordered fallback list', async () => {
  const app = await desk()
  button(app.render(), 'Edit assignment').props.onClick()
  button(app.render(), 'Set primary').props.onClick()
  assert.match(text(app.render()), /PrimaryBeta/)
  assert.match(text(app.render()), /No explicit fallbacks/)
  assert.equal(button(app.render(), 'Save shared assignment').props.disabled, false)
  assert.equal(app.writes.length, 0)
})

test('catalog paging and search select real fixture matches without saving', async () => {
  const app = await desk()
  button(app.render(), 'Next models').props.onClick()
  assert.match(text(app.render()), /Gamma/)
  const search = findAll(app.render(), node => node.props?.['aria-label'] === 'Search model catalog')[0]
  search.props.onChange({ target: { value: 'Alpha' } })
  assert.match(text(app.render()), /1 matching models/)
  assert.equal(button(app.render(), 'Previous models').props.disabled, true)
  assert.equal(button(app.render(), 'Next models').props.disabled, true)
  assert.equal(app.writes.length, 0)
})

test('an assignment changed elsewhere blocks saving a stale local draft', async () => {
  const app = await desk()
  button(app.render(), 'Edit assignment').props.onClick()
  button(app.render(), 'Use account default').props.onClick()
  await app.advanceRevision()
  assert.match(text(app.render()), /changed elsewhere/)
  assert.equal(button(app.render(), 'Save shared assignment').props.disabled, true)
  assert.equal(app.writes.length, 0)
})

function floor(data, workflow = 'brief', extra = {}) {
  const state = []
  let slot = 0
  const calls = []
  const exports = {}
  vm.runInNewContext(outputText, {
    exports,
    require: name => name === 'react' ? {
      useState: initial => {
        const index = slot++
        if (!(index in state)) state[index] = initial
        return [state[index], next => { state[index] = typeof next === 'function' ? next(state[index]) : next }]
      },
      useEffect: () => {},
    } : name === 'react/jsx-runtime' ? require(name) : name.endsWith('/agent-card-hands') ? registry : {},
  })
  const props = {
    user: { id: 'fixture-founder', role: 'founder' }, data, workflow, receipts: [], actions: [], evolution: true, ...extra,
    onOpen: value => calls.push(['workflow', value]), onNavigate: value => calls.push(['navigate', value]),
    onOpenConversation: value => calls.push(['conversation', value]), onOpenBooking: value => calls.push(['booking', value]),
  }
  return { render: () => { slot = 0; return exports.FounderBoard(props) }, calls, props }
}

test('Saishin cards use their assigned shells and estimate controls calculate without writes', () => {
  const empty={status:'loading',data:null}
  const app=floor({inbox:empty,bookings:empty},'roster')
  button(app.render(),'Inspect Saishin').props.onClick()
  app.props.workflow='craft'
  button(app.render(),'Estimate').props.onClick()
  assert.equal(findAll(app.render(),n=>n.props?.['data-agent-action-shell']==='receipt-tape').length,1)
  findAll(app.render(),n=>n.props?.['aria-label']==='Estimated hours')[0].props.onChange({target:{value:'2'}})
  findAll(app.render(),n=>n.props?.['aria-label']==='Hourly rate in USD')[0].props.onChange({target:{value:'150'}})
  assert.match(text(app.render()), /\$300.00/)
  assert.equal(app.calls.length,0)
  button(app.render(),'Close agent card').props.onClick()
  assert.equal(findAll(app.render(),n=>n.props?.['data-agent-action-shell']).length,0)
  button(app.render(),'Hold a slot').props.onClick()
  assert.equal(findAll(app.render(),n=>n.props?.['data-agent-action-shell']==='chair-ticket').length,1)
  app.props.workflow='roster'
  button(app.render(),'Inspect Ensei').props.onClick()
  app.props.workflow='craft'
  assert.equal(findAll(app.render(),n=>n.props?.['data-agent-action-shell']).length,0)
})

test('cast workflows carry the selected agent and real child controls into the assigned shell',()=>{
  const empty={status:'loading',data:null}
  const app=floor({inbox:empty,bookings:empty},'roster')
  app.props.actions=[{id:'access',title:'Scopes & tools',description:'Manage access',shell:'courier-manifest'}]
  button(app.render(),'Inspect Ensei').props.onClick()
  app.props.workflow='access'
  const wrapper=findAll(app.render(),node=>node.type?.name==='ActionCardWorkspace')[0]
  assert.equal(wrapper.props.agent[0],'bella')
  assert.equal(wrapper.props.action.shell,'courier-manifest')
  assert.equal(findAll(wrapper,node=>node.type?.name==='AccessEditor')[0].props.agentId,'bella')
  app.props.workflow='conversations'
  app.props.renderConversations=agent=>({type:'section',props:{'data-list-owner':agent}})
  assert.equal(findAll(app.render(),node=>node.props?.['data-list-owner']==='bella').length,1)
})

test('workshop owns card controls and homepage is a separate non-castable overview', () => {
  const empty={status:'loading',data:null}
  const app=floor({inbox:empty,bookings:empty},'roster')
  button(app.render(),'Inspect Ensei').props.onClick()
  app.props.workflow='craft'
  assert.equal(findAll(app.render(),n=>n.props?.['data-card-workshop']==='bella').length,1)
  app.props.workflow='models'
  const tree=app.render()
  assert.equal(findAll(tree,n=>n.props?.['data-card-workshop']).length,0)
  assert.equal(findAll(tree,n=>n.type?.name==='ModelRoutingEditor')[0].props.agentId,'bella')
  app.props.workflow=null
  assert.equal(findAll(app.render(),n=>n.props?.['data-card-workshop']).length,0)
  assert.equal(findAll(app.render(),n=>n.props?.['data-hub-overview']!==undefined).length,1)
  assert.doesNotMatch(text(app.render()),/Card title|Memento|Workbench/)
})

test('workshop drafts update previews without changing the shared assigned registry',()=>{
  const empty={status:'loading',data:null}
  const app=floor({inbox:empty,bookings:empty},'craft')
  button(app.render(),'Report up').props.onClick()
  const input=()=>findAll(app.render(),n=>n.props?.['aria-label']==='Card title')[0]
  input().props.onChange({target:{value:'Daily report'}})
  assert.equal(input().props.value,'Daily report')
  assert.equal(registry.agentCardHands.gala[0].name,'Report up')
  button(app.render(),'Reset presentation').props.onClick()
  assert.equal(input().props.value,'Report up')
})

test('homepage attention controls use loaded records and never infer a full-shop all clear',()=>{
  const app=floor({inbox:{status:'ready',data:{total:10,items:[{id:'thread-1',name:'Client',unread_count:1}]}},bookings:{status:'error',data:null,error:'Offline'}},null)
  assert.match(text(app.render()),/not an all-clear/)
  button(app.render(),'Open ClientRead the thread to review the request.').props.onClick()
  assert.deepEqual(app.calls,[['conversation','thread-1']])
})

test('roster is exclusive to Orchestra management and selection survives other cards',()=>{
  const empty={status:'loading',data:null}
  const app=floor({inbox:empty,bookings:empty},'roster')
  button(app.render(),'Inspect Saishin').props.onClick()
  for (const workflow of [null,'conversations','models','access','train','brief','runs','dispatch','build','decide']) {
    app.props.workflow=workflow
    assert.equal(findAll(app.render(),n=>n.props?.['data-orchestra-roster']!==undefined).length,0)
    button(app.render(),'Manage Orchestra').props.onClick()
  }
  app.props.workflow='roster'
  assert.equal(button(app.render(),'Inspect Saishin').props['aria-pressed'],true)
})

test('Floor prioritizes a loaded unread conversation and opens that exact record', () => {
  const app = floor({
    inbox: { status: 'ready', data: { total: 100, items: [
      { id: 'read', name: 'Already read', unread_count: 0, is_unread: false },
      { id: 'unread', name: 'Needs a reply', unread_count: 1, is_unread: false, last_message: { content: 'Can I book a consultation?' } },
    ] } },
    bookings: { status: 'ready', data: { total: 0, items: [] } },
  })
  assert.match(text(app.render()), /1 unread in 2 loaded threads/)
  assert.match(text(app.render()), /Can I book a consultation/)
  button(app.render(), 'Open this conversation ↗').props.onClick()
  assert.deepEqual(app.calls, [['conversation', 'unread']])
})

test('Floor booking lane opens the pending record and selected-agent tools keep workflow ownership', () => {
  const app = floor({
    inbox: { status: 'ready', data: { total: 0, items: [] } },
    bookings: { status: 'ready', data: { total: 2, items: [
      { id: 'confirmed', status: 'confirmed', resolved_name: 'Confirmed client' },
      { id: 'pending', status: 'pending', resolved_name: 'Pending client', description: 'Traditional eagle' },
    ] } },
  })
  button(app.render(), 'Review bookings1 pending in 2 loaded bookings').props.onClick()
  assert.match(text(app.render()), /Traditional eagle/)
  button(app.render(), 'Review this booking ↗').props.onClick()
  button(app.render(), 'Open Bench').props.onClick()
  assert.deepEqual(app.calls, [['booking', 'pending'], ['workflow', 'train']])
})

test('Floor shows an unavailable snapshot honestly and keeps generic navigation usable', () => {
  const app = floor({
    inbox: { status: 'error', data: null, error: 'Inbox could not be loaded.' },
    bookings: { status: 'loading', data: null, error: null },
  })
  assert.match(text(app.render()), /Inbox could not be loaded/)
  assert.doesNotMatch(text(app.render()), /0 unread/)
  button(app.render(), 'Open Courier ↗').props.onClick()
  assert.deepEqual(app.calls, [['navigate', 'courier']])
})

test('agent tiles toggle stats independently of refresh and switching selects the new accordion', () => {
  const empty = {status:'loading',data:null}
  const app = floor({inbox:empty,bookings:empty}, 'roster')
  assert.equal(button(app.render(), 'Inspect Gala').props['aria-expanded'], false)
  button(app.render(), 'Inspect Gala').props.onClick()
  assert.equal(button(app.render(), 'Inspect Gala').props['aria-expanded'], true)
  assert.equal(findAll(app.render(), node => node.props?.id === 'agent-stats-gala').length, 1)
  button(app.render(), 'Refresh Gala state').props.onClick()
  assert.equal(button(app.render(), 'Inspect Gala').props['aria-expanded'], true)
  button(app.render(), 'Inspect Gala').props.onClick()
  assert.equal(findAll(app.render(), node => node.props?.id === 'agent-stats-gala').length, 0)
  button(app.render(), 'Inspect Ensei').props.onClick()
  assert.equal(button(app.render(), 'Inspect Ensei').props['aria-expanded'], true)
  assert.equal(button(app.render(), 'Inspect Gala').props['aria-expanded'], false)
  assert.doesNotMatch(text(app.render()), /Agent stats/)
})

test('Bandit banner deals 3 cards and seals one only from a real tool receipt', () => {
  assert.equal(registry.RYOZAN_BANDIT_ORCHESTRA.length, 9)
  const empty = { status: 'loading', data: null }
  const app = floor({ inbox: empty, bookings: empty }, 'roster')
  button(app.render(), 'Inspect Shishin').props.onClick()
  let tree = app.render()
  assert.equal(findAll(tree, n => n.props?.['data-npc-dialogue-banner'] === 'shishin').length, 1)
  assert.match(text(tree), /Nine-Tattooed Dragon/)
  assert.match(text(tree), /🔥 Super/)
  assert.equal(findAll(tree, n => n.props?.['data-dealt-card'] !== undefined).length, 3)
  // Opening the board never fabricates an execution.
  assert.equal(findAll(tree, n => n.props?.['data-cast-bandit-card'] !== undefined).length, 0)
  assert.equal(findAll(tree, n => n.props?.['data-hanko-seal'] === 'executed').length, 0)
  app.props.receipts = [{ tool: 'lookup_client', status: 'succeeded', correlation_id: 'gala-fixture-1' }]
  tree = app.render()
  assert.equal(findAll(tree, n => n.props?.['data-hanko-seal'] === 'executed').length, 1)
  assert.match(text(tree), /済 · gala-fixture-1/)
  app.props.receipts = [{ tool: 'lookup_client', status: 'failed', correlation_id: 'gala-fixture-2' }]
  assert.equal(findAll(app.render(), n => n.props?.['data-hanko-seal'] === 'executed').length, 0)
})

test('a standalone install hides Ark-served Bench and trial controls', () => {
  const app = floor({
    inbox: { status: 'ready', data: { total: 0, items: [] } },
    bookings: { status: 'ready', data: { total: 0, items: [] } },
  }, 'brief', { evolution: false })
  assert.equal(findAll(app.render(), n => n.type === 'button' && text(n) === 'Open Bench').length, 0)
  assert.match(text(app.render()), /runs nvidia\//)
})
