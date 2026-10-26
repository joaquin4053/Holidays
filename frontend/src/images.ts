// Place photos. 548 destinations carry a fixed Wikimedia `img`; the others only name a Wikipedia article
// (`wiki`), whose photo is looked up once via the Wikipedia REST API: at most 2 requests at a time,
// backoff on 429/errors, cached in localStorage. Before launch these should be resolved on the server
// once (with photo credits), see design_handoff_holydays/README.md.
import { useSyncExternalStore } from 'react'
import type { Destination } from './mapdata'

const KEY = 'holydays-img-cache-v2'
let cache: Record<string, string> = {}
try { cache = JSON.parse(localStorage.getItem(KEY) || '{}') } catch { cache = {} }

const queue: string[] = []
const tries: Record<string, number> = {}
let busy = 0, paused = false, version = 0
const listeners = new Set<() => void>()
let notifyTimer: ReturnType<typeof setTimeout> | undefined

function notify() {
  clearTimeout(notifyTimer)
  notifyTimer = setTimeout(() => { version++; listeners.forEach(l => l()) }, 250)
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(cache)) } catch { /* storage full or blocked */ } }

function retryLater(title: string) {
  const n = tries[title] = (tries[title] || 0) + 1
  if (n > 3) return
  paused = true
  setTimeout(() => { paused = false; queue.push(title); pump() }, [3000, 10000, 30000][n - 1])
}

function pump() {
  if (busy >= 2 || !queue.length || paused) return
  const title = queue.shift()!
  busy++
  fetch('https://en.wikipedia.org/api/rest_v1/page/summary/' + encodeURIComponent(title.replace(/ /g, '_')))
    .then(r => {
      if (r.status === 404) { cache[title] = ''; save(); return }
      if (!r.ok) { retryLater(title); return }
      return r.json().then(j => {
        const o = j.originalimage, th = j.thumbnail?.source as string | undefined
        cache[title] = o && o.width <= 1400 ? o.source : th ? th.replace(/\/\d+px-/, '/960px-') : ''
        save()
      })
    })
    .catch(() => retryLater(title))
    .finally(() => { busy--; notify(); setTimeout(pump, 250) })
}

/** Photo URL for a destination ('' until known or when there is none). */
export function imageFor(d: Destination | undefined | null): string {
  if (!d) return ''
  if (d.img) return d.img
  if (!d.wiki) return ''
  const c = cache[d.wiki]
  if (c !== undefined) return c
  if (!queue.includes(d.wiki)) { queue.push(d.wiki); setTimeout(pump, 0) }
  return ''
}

/** Re-render when newly looked-up photos arrive. */
export function useImageVersion(): number {
  return useSyncExternalStore(cb => { listeners.add(cb); return () => listeners.delete(cb) }, () => version)
}
