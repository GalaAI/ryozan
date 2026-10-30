import { createElement, Fragment, type CSSProperties, type ReactNode } from 'react'

/** The approved DC uses property paths, not expressions. Never evaluate source
 * or API strings as JavaScript; values can only enter React's escaped props. */
export type Values = Record<string, unknown>
function resolve(values: Values, path: string): unknown {
  const key = path.trim()
  if (key === 'true') return true
  if (key === 'false') return false
  if (!/^[\w.]+$/.test(key)) return undefined
  return key.split('.').reduce<unknown>((value, part) => {
    if (['__proto__', 'prototype', 'constructor'].includes(part)) return undefined
    return value && typeof value === 'object' ? (value as Values)[part] : undefined
  }, values)
}
function value(raw: string, values: Values): unknown {
  const whole = raw.match(/^\s*{{([^}]+)}}\s*$/)
  if (whole) return resolve(values, whole[1])
  return raw.replace(/{{([^}]+)}}/g, (_, key: string) => String(resolve(values, key) ?? ''))
}
const camel = (key: string) => key.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())
function style(raw: string): CSSProperties {
  const result: Record<string, string> = {}
  for (const declaration of raw.split(';')) {
    const colon = declaration.indexOf(':')
    if (colon < 0) continue
    const key = declaration.slice(0, colon).trim()
    result[key.startsWith('--') ? key : camel(key)] = declaration.slice(colon + 1).trim()
  }
  return result as CSSProperties
}
type Builder = (values: Values) => ReactNode
export function compileTemplate(source: string): { render: Builder; css: string } {
  const template = document.createElement('template')
  // Only a bundled, reviewed template enters this parser. No scripts execute.
  template.innerHTML = source.replace(/\s(on[A-Z]\w*|viewBox|preserveAspectRatio|baseFrequency|numOctaves|stitchTiles|ref)=/g, ' data-prop-$1=')
  const pseudo: string[] = []
  let serial = 0
  function compile(node: Node): Builder {
    const id = serial++
    if (node.nodeType === Node.TEXT_NODE) {
      // Adjacent slots may contain React controls, not just strings. Resolve
      // them separately so an image/input is never reduced to [object Object].
      const parts = (node.textContent ?? '').split(/({{[^}]+}})/g)
      return vals => parts.map((part, index) => <Fragment key={index}>{value(part, vals) as ReactNode}</Fragment>)
    }
    if (!(node instanceof Element)) return () => null
    const tag = node.localName
    if (['script', 'helmet', 'iframe', 'object', 'embed'].includes(tag)) return () => null
    const children = Array.from(node.childNodes).map(compile)
    if (tag === 'sc-if') return vals => value(node.getAttribute('value') ?? '', vals) ? children.map((child, i) => <Fragment key={i}>{child(vals)}</Fragment>) : null
    if (tag === 'sc-for') return vals => {
      const items = value(node.getAttribute('list') ?? '', vals)
      if (!Array.isArray(items)) return null
      const name = node.getAttribute('as') ?? 'item'
      return items.map((item, i) => <Fragment key={String((item as Values)?.id ?? i)}>{children.map((child, j) => <Fragment key={j}>{child({ ...vals, [name]: item })}</Fragment>)}</Fragment>)
    }
    const attrs = Array.from(node.attributes)
    const hover = node.getAttribute('style-hover')
    if (hover && !hover.includes('{{')) pseudo.push(`.dc-native-${id}:hover{${hover}}`)
    return vals => {
      const props: Values = { key: id }
      for (const attr of attrs) {
        let key = attr.name
        if (key.startsWith('hint-') || key.startsWith('style-')) continue
        if (key.startsWith('data-prop-')) {
          key = key.slice(10)
          const events: Record<string, string> = { onclick:'onClick', onchange:'onChange', onmousedown:'onMouseDown', onmouseenter:'onMouseEnter', onmouseleave:'onMouseLeave', onmousemove:'onMouseMove', ondragover:'onDragOver', ondragleave:'onDragLeave', ondrop:'onDrop', onkeydown:'onKeyDown', onsubmit:'onSubmit', viewbox:'viewBox', preserveaspectratio:'preserveAspectRatio', basefrequency:'baseFrequency', numoctaves:'numOctaves', stitchtiles:'stitchTiles' }
          key = events[key] ?? key
        }
        let resolved = value(attr.value, vals)
        if (key.startsWith('on') && typeof resolved !== 'function') { if (tag === 'button') props.disabled = true; continue }
        if (key === 'ref' && !resolved) continue
        if (key === 'class') key = 'className'
        if (key === 'for') key = 'htmlFor'
        if (key === 'tabindex') key = 'tabIndex'
        if (key === 'style') resolved = style(String(resolved ?? ''))
        if (['src', 'href'].includes(key) && typeof resolved === 'string') {
          if (/^(javascript|data):/i.test(resolved)) continue
          if (resolved.startsWith('assets/')) resolved = '/desktop-portal/' + resolved
        }
        if (key.includes('-') && !key.startsWith('data-') && !key.startsWith('aria-')) key = camel(key)
        props[key] = resolved
      }
      props.className = `${props.className ?? ''} dc-native-${id}`
      if (tag === 'img' && props.onClick) { props.role = 'button'; props.tabIndex = 0; props.onKeyDown = (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); (props.onClick as () => void)() } } }
      return createElement(tag, props, ...children.map(child => child(vals)))
    }
  }
  const builders = Array.from(template.content.childNodes).map(compile)
  return { render: vals => builders.map((builder, i) => <Fragment key={i}>{builder(vals)}</Fragment>), css: pseudo.join('\n') }
}
