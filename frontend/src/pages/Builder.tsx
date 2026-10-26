// Trip builder (design 6a, which also contains the approved homepage 4a): world map, search with
// destination cards, then a 3-step planner (route → basics → preferences) that POSTs /api/trips.
// Ported from design_handoff_holydays/Holydays Trip Builder.dc.html; sizes from its 1280 x 800 frame
// are mapped onto the browser window.
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, type GeoPlace, type Tier, type TripRequest, type TripType } from '../api'
import { imageFor, useImageVersion } from '../images'
import {
  arc, CITIES, CONT_NAME, CONT_POS, CONTINENTS, fmtKm, km, MAP_W, MAPCOL, norm, toPt, unproj,
  useCountries, useDestinations, type Destination, type Pt,
} from '../mapdata'

const TRIP_TYPES: [TripType, string][] = [['city', 'City trip'], ['culture', 'Culture'], ['beach', 'Beach & relax'],
  ['adventure', 'Adventure & nature'], ['backpacking', 'Backpacking'], ['roadtrip', 'Road trip'], ['family', 'Family']]
const INTERESTS = ['Culture & history', 'Food', 'Nature', 'Nightlife', 'Shopping', 'Art & museums', 'Architecture',
  'Beaches', 'Hiking', 'Photography', 'Local markets', 'Wellness']
const TIERS: [Tier, string, string][] = [['budget', 'Budget', 'Hostels, local food'], ['comfort', 'Comfort', 'Mid-range hotels'],
  ['luxury', 'Luxury', 'Top hotels, fine dining']]
const MOODS = ['All', 'Beach', 'City', 'Nature'] as const
const SEASONS = ['Spring', 'Summer', 'Autumn', 'Winter'] as const
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const INK = '#24231f', MUTED = '#6b675e', CARD = '#fffdf8', LINE = '#ece6da'
const FONT = "'Bricolage Grotesque', sans-serif"
const PAGE_SIZE = 36  // destination cards rendered at once (1005 in total)

interface Stop extends Pt { nights: number | null }
interface Plan {
  origin: Pt; stops: Stop[]; back: boolean; days: number; budget: number; tier: Tier
  tripType: TripType; interests: string[]; guests: number; from: string; to: string
}
const PLAN0: Plan = { origin: CITIES[0], stops: [], back: true, days: 14, budget: 6000, tier: 'comfort',
  tripType: 'city', interests: [], guests: 2, from: '', to: '' }
const STORE = 'holydays-builder'

function loadPlan(): Plan {
  try { return { ...PLAN0, ...JSON.parse(sessionStorage.getItem(STORE) || '{}') } } catch { return PLAN0 }
}

function useViewport() {
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight })
  useEffect(() => {
    const on = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return vp
}

const pad2 = (n: number) => String(n).padStart(2, '0')
const iso = (y: number, m: number, d: number) => `${y}-${pad2(m + 1)}-${pad2(d)}`
const fmtDay = (k: string) => new Date(k + 'T00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`

export default function Builder() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { w: W, h: H } = useViewport()
  const countries = useCountries()
  const dest = useDestinations()
  useImageVersion()

  const [P, setP] = useState<Plan>(loadPlan)
  const set = useCallback((p: Partial<Plan>) => setP(s => ({ ...s, ...p })), [])
  const setStops = (f: (s: Stop[]) => Stop[]) => setP(s => ({ ...s, stops: f(s.stops.slice()) }))
  useEffect(() => { try { sessionStorage.setItem(STORE, JSON.stringify(P)) } catch { /* storage blocked */ } }, [P])

  const [plan, setPlan] = useState(false)
  const [step, setStep] = useState(1)
  const [open, setOpen] = useState(false)             // search opened (4a open state)
  const [q, setQ] = useState('')                      // "Where to?"
  const [mood, setMood] = useState<string>('All')
  const [season, setSeason] = useState<string>('Summer')
  const [cal, setCal] = useState(false)
  const [calM, setCalM] = useState<number | null>(null)
  const [shown, setShown] = useState(PAGE_SIZE)
  const [wf, setWf] = useState(false)                 // "Where to?" focused
  const [sq, setSq] = useState(''); const [sugOpen, setSugOpen] = useState(false)   // add-a-stop input
  const [oq, setOq] = useState(''); const [osugOpen, setOsugOpen] = useState(false) // origin input
  const [geo, setGeo] = useState<{ q: string; list: GeoPlace[] | null; ok: boolean } | null>(null)
  const [hov, setHov] = useState<number | null>(null)
  const [near, setNear] = useState('')
  const [drag, setDrag] = useState<{ from: number; over: number; dy: number } | null>(null)
  const [dropT, setDropT] = useState(false)
  const [send, setSend] = useState<{ state: 'sending' | 'error'; msg: string } | null>(null)
  const dragM = useRef<{ y0: number; tops: number[]; hs: number[] } | null>(null)
  const openedAt = useRef(0)
  const geoTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const asideRef = useRef<HTMLElement>(null)

  // --- prefill from ?trip=<id> ("Edit route" on the results page) ---------------------------------------
  const tripParam = params.get('trip')
  useEffect(() => {
    if (!tripParam) return
    let live = true
    api.trip(tripParam).then(async t => {
      const r = t.request
      const pool = [...CITIES, ...(dest || []).filter(d => d.lat != null).map(d => toPt({ ...d, lat: d.lat!, lon: d.lon! }))]
      const find = async (n: string): Promise<Pt | null> => {
        const hit = pool.find(p => norm(p.name) === norm(n))
        if (hit) return hit
        try {
          const [g] = await api.search(n)
          if (g) return toPt({ name: g.name || n, country: g.country, c: contOf(g.country), lat: g.lat, lon: g.lon })
        } catch { /* backend down */ }
        return null
      }
      const origin = await find(r.origin)
      const stops = (await Promise.all(r.stops.map(s => find(s.name).then(p => p && { ...p, nights: s.nights ?? null }))))
        .filter((s): s is Stop => !!s)
      if (!live) return
      setP(s => ({ ...s, origin: origin || s.origin, stops, back: r.return_to_origin !== false,
        days: r.days || (r.start_date && r.end_date ? Math.round((Date.parse(r.end_date) - Date.parse(r.start_date)) / 864e5) + 1 : s.days),
        budget: Math.round(r.budget || s.budget), tier: r.tier || s.tier, tripType: r.trip_type || s.tripType,
        interests: r.interests || [], guests: r.travelers || s.guests, from: r.start_date || '', to: r.end_date || '' }))
      setOpen(false); setPlan(true); setStep(1)
    }).catch(e => console.warn('prefill', e))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tripParam, !!dest])

  const cmap = useMemo(() => {
    const m: Record<string, string> = { 'United States': 'namerica', 'Czech Republic': 'europe', Turkey: 'asia', 'Türkiye': 'asia' }
    ;(countries || []).forEach(k => { m[k.n] = k.c })
    return m
  }, [countries])
  function contOf(country: string): string | undefined { return cmap[country] || CITIES.find(c => c.country === country)?.c }

  const pool = useMemo(() => [...CITIES, ...(dest || []).filter(d => d.lat != null)
    .map(d => toPt({ name: d.name, country: d.country, c: d.c, lat: d.lat!, lon: d.lon! }))], [dest])

  // --- escape closes the search ---------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      ;(document.activeElement as HTMLElement | null)?.blur()
      setOpen(false); setQ(''); setMood('All'); setCal(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const geoSearch = (v: string) => {
    clearTimeout(geoTimer.current)
    const t = v.trim()
    if (t.length < 2) return
    geoTimer.current = setTimeout(() => {
      api.search(t).then(list => setGeo({ q: t, list: Array.isArray(list) ? list : [], ok: true }))
        .catch(() => setGeo({ q: t, list: null, ok: false }))
    }, 180)
  }

  // --- route --------------------------------------------------------------------------------------------
  const O = P.origin, all: Pt[] = [O, ...P.stops], n = P.stops.length, last = all[all.length - 1]
  const has = (name: string) => P.stops.some(s => s.name === name)
  const add = (p: Pt | null) => {
    if (!p) return
    if (p.name === O.name || km(O, p) < 25) { setSq(''); setSugOpen(false); return }
    setSq(''); setSugOpen(false)
    setP(s => ({ ...s, stops: s.stops.some(t => t.name === p.name) ? s.stops : [...s.stops, { ...p, nights: null }] }))
  }
  const removeStop = (name: string) => setStops(st => st.filter(s => s.name !== name))
  const toggleStop = (p: Pt) => (has(p.name) ? removeStop(p.name) : add(p))
  const fromGeo = (g: GeoPlace): Pt => toPt({ name: g.name, country: g.country || '', c: contOf(g.country || ''), lat: g.lat, lon: g.lon })

  // --- map geometry: the design's 1650 px map in a 1280 x 800 frame, scaled to cover the window ---------
  const k = Math.max(W / 1280, H / 800)
  const mapW = 1650 * k, S = mapW / MAP_W
  const left0 = W / 2 - 825 * k, top0 = H / 2 - 398 * k
  let tx = 0, ty = 0, sc = 1
  if (plan) {
    const fit = P.stops.length ? P.stops : all
    const xs = fit.map(p => p.x * S), ys = fit.map(p => p.y * S), pad = 130
    const A = { x: 470, y: 90, w: Math.max(200, W - 500), h: Math.max(200, H - 110) }
    const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad
    sc = Math.max(0.55, Math.min(2.6, A.w / (x1 - x0), A.h / (y1 - y0)))
    tx = A.x - left0 + (A.w - (x1 - x0) * sc) / 2 - x0 * sc
    ty = A.y - top0 + (A.h - (y1 - y0) * sc) / 2 - y0 * sc
  }
  const scr = (x: number, y: number): [number, number] => [left0 + tx + x * S * sc, top0 + ty + y * S * sc]
  const seq = [...all, ...(P.back && n ? [O] : [])]
  const legs = seq.slice(1).map((b, i) => ({ d: km(seq[i], b), path: arc(seq[i], b) }))
  const showRoute = plan || (!open && n > 0)

  // --- nights ---------------------------------------------------------------------------------------------
  const dated = !!(P.from && P.to)
  const total = dated ? Math.round((Date.parse(P.to) - Date.parse(P.from)) / 864e5) : P.days - 1
  const fixed = P.stops.reduce((a, s) => a + (s.nights || 0), 0), autoN = P.stops.filter(s => !s.nights).length
  const share = autoN ? Math.max(0, Math.floor((total - fixed) / autoN)) : 0
  const nightsOf = (s: Stop) => s.nights || share

  // --- suggestions ----------------------------------------------------------------------------------------
  const liveFor = (raw: string): Pt[] | null => {
    const g = geo && geo.ok && geo.q.toLowerCase() === raw ? geo.list : null
    return g && g.map(fromGeo)
  }
  const localFor = (raw: string) => pool.filter(p => (p.name + ' ' + p.country).toLowerCase().includes(raw))
  const sqn = sq.trim().toLowerCase(), names = new Set(all.map(p => p.name))
  const sqLive = sqn ? liveFor(sqn) : null
  const sug = (sqn ? (sqLive || localFor(sqn)).slice(0, 6)
    : pool.filter(p => !names.has(p.name) && km(last, p) > 80).sort((a, b) => km(last, a) - km(last, b)).slice(0, 5))
  const oqn = oq.trim().toLowerCase(), oLive = oqn ? liveFor(oqn) : null
  const osug = (oqn ? (oLive || localFor(oqn)) : CITIES).slice(0, 6)
  const geoDown = !!(geo && !geo.ok)
  const wqn = q.trim().toLowerCase()
  const whereSug = (wqn.length >= 2 && geo?.ok && geo.q.toLowerCase() === wqn ? geo.list || [] : []).slice(0, 6)

  // --- destination cards ----------------------------------------------------------------------------------
  const res = useMemo(() => {
    let r = (dest || []).filter(d => mood === 'All' || d.mood === mood)
    if (wqn) r = r.filter(d => [d.name, d.country, d.cn, CONT_NAME[d.c]].some(t => (t || '').toLowerCase().includes(wqn)))
    return r
  }, [dest, mood, wqn])
  useEffect(() => setShown(PAGE_SIZE), [mood, wqn])
  const destPt = (d: Destination): Pt | null => d.lat == null ? null : toPt({ name: d.name, country: d.country, c: d.c, lat: d.lat, lon: d.lon! })

  // --- calendar -------------------------------------------------------------------------------------------
  const now = new Date(), curM = now.getFullYear() * 12 + now.getMonth()
  const today = iso(now.getFullYear(), now.getMonth(), now.getDate())
  const baseM = Math.max(curM, calM ?? curM)
  const pickDay = (d: string) => {
    if (!P.from || P.to || d < P.from) set({ from: d, to: '' })
    else if (d === P.from) set({ from: '', to: '' })
    else set({ to: d })
  }
  const calNights = dated ? total : 0
  const dateText = P.from ? fmtDay(P.from) + ' – ' + (P.to ? fmtDay(P.to) : 'Add return') : 'Add dates'

  // --- actions --------------------------------------------------------------------------------------------
  const blur = () => (document.activeElement as HTMLElement | null)?.blur()
  const openSearch = () => { if (!open) { openedAt.current = Date.now(); setOpen(true) } }
  const closeSearch = () => { blur(); setOpen(false); setQ(''); setMood('All'); setCal(false) }
  const openPlan = () => { blur(); setOpen(false); setCal(false); setPlan(true); setHov(null) }
  const browse = () => { setPlan(false); openSearch() }
  const pickCountry = (i: number) => {
    const c = countries![i]
    if (plan) {
      const x = c.x * 13.76, y = c.y * 7.68, [lat, lon] = unproj(x, y)
      add({ name: c.n, country: c.n, c: c.c, lat, lon, x, y })
    } else { openSearch(); setQ(c.n) }
  }
  const onBgClick = (e: React.MouseEvent) => {
    if (Date.now() - openedAt.current < 600) return
    const t = e.target as HTMLElement
    if (plan) { if (!t.closest('aside,button,input,label,a,[data-keep],path')) browse(); return }
    if (open && !t.closest('button,input,label,a,[data-keep]')) closeSearch()
  }
  const next = () => { if (n) { setStep(s => Math.min(3, s + 1)); setSq(''); setSugOpen(false); setOq(''); setOsugOpen(false) } }
  const back = () => setStep(s => Math.max(1, s - 1))

  const submit = async () => {
    if (!n || fixed > total || send?.state === 'sending') return
    const req: TripRequest = {
      origin: O.name, stops: P.stops.map(s => ({ name: s.name, nights: s.nights || null })),
      return_to_origin: P.back, travelers: P.guests, budget: Math.max(1, P.budget), currency: 'CHF', tier: P.tier,
      trip_type: P.tripType, interests: P.interests,
      start_date: dated ? P.from : null, end_date: dated ? P.to : null, days: dated ? null : P.days,
    }
    setSend({ state: 'sending', msg: 'Sending your trip…' })
    try {
      const { id } = await api.createTrip(req)
      navigate(`/trip/${id}`)
    } catch (e) {
      const m = String((e as Error).message || e)
      setSend({ state: 'error', msg: m.includes('fetch') ? 'Backend not reachable.' : m })
    }
  }

  // --- drag to reorder ------------------------------------------------------------------------------------
  const pDown = (i: number) => (ev: RPointerEvent<HTMLDivElement>) => {
    if (ev.button !== 0 || (ev.target as HTMLElement).closest('button,input')) return
    const rows = [...(asideRef.current?.querySelectorAll<HTMLElement>('[data-stop]') || [])]
    dragM.current = { y0: ev.clientY, tops: rows.map(r => r.offsetTop), hs: rows.map(r => r.offsetHeight) }
    ev.currentTarget.setPointerCapture(ev.pointerId)
    setDrag({ from: i, over: i, dy: 0 })
  }
  const pMove = (i: number) => (ev: RPointerEvent<HTMLDivElement>) => {
    const m = dragM.current
    if (!drag || drag.from !== i || !m) return
    const dy = ev.clientY - m.y0, c = m.tops[i] + m.hs[i] / 2 + dy
    let over = i
    m.tops.forEach((t, j) => { const mid = t + m.hs[j] / 2; if (j > i && c > mid) over = Math.max(over, j); if (j < i && c < mid) over = Math.min(over, j) })
    setDrag({ ...drag, dy, over })
  }
  const pUp = (i: number) => () => {
    if (!drag || drag.from !== i) return
    dragM.current = null
    const d = drag
    setDrag(null); setDropT(true)
    if (d.over !== d.from) setStops(st => { const [mv] = st.splice(d.from, 1); st.splice(d.over, 0, mv); return st })
    requestAnimationFrame(() => requestAnimationFrame(() => setDropT(false)))
  }
  const rowMotion = (i: number): CSSProperties => {
    const m = dragM.current
    if (!drag || !m) return { transform: 'translateY(0px)', transition: dropT ? 'none' : 'transform .28s cubic-bezier(.2,.8,.2,1), box-shadow .2s', zIndex: 1, cursor: 'grab' }
    if (i === drag.from) return { transform: `translateY(${drag.dy}px) scale(1.02)`, transition: 'box-shadow .2s', zIndex: 5, boxShadow: '0 14px 34px rgba(40,30,20,.18)', cursor: 'grabbing' }
    const Hh = m.hs[drag.from]
    const off = drag.from < drag.over && i > drag.from && i <= drag.over ? -Hh : drag.over < drag.from && i >= drag.over && i < drag.from ? Hh : 0
    return { transform: `translateY(${off}px)`, transition: 'transform .28s cubic-bezier(.2,.8,.2,1)', zIndex: 1, cursor: 'grabbing' }
  }

  // --- pins -----------------------------------------------------------------------------------------------
  const placed: [number, number][] = []
  const pins = showRoute ? all.map((p, i) => {
    const [X, Y] = scr(p.x, p.y)
    const crowded = !plan && placed.some(([a, b]) => Math.abs(a - X) < 90 && Math.abs(b - Y) < 26)
    placed.push([X, Y])
    return { p, i, X, Y, crowded }
  }) : []
  const hovC = hov != null && countries ? countries[hov] : null
  const hovPlan = plan && hovC ? scr(hovC.x * 13.76, hovC.y * 7.68) : null

  // --- search bar placement -------------------------------------------------------------------------------
  const barTop = open ? 84 : plan ? H + 80 : H - 100
  const barW = open ? Math.min(1200, W - 80) : Math.min(640, W - 48)

  const disabled = '#cfc9bd'
  const stepTitle = ['Your route', 'Basics', 'Preferences'][step - 1]

  return (
    <div className="hb" onClick={onBgClick} style={{ position: 'fixed', inset: 0, overflow: 'hidden', background: '#f7f5f0', fontFamily: FONT, color: INK }}>
      <header style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 5, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '22px 40px', pointerEvents: 'none' }}>
        <div onClick={() => { setPlan(false); setHov(null) }} style={{ font: `800 28px ${FONT}`, letterSpacing: '-.03em', cursor: 'pointer', pointerEvents: 'auto' }}>Holydays</div>
        <button className="hb-outline" style={{ border: `1px solid ${INK}`, background: 'transparent', padding: '9px 18px', borderRadius: 999, fontSize: 14, pointerEvents: 'auto' }}>Sign in</button>
      </header>

      {/* map */}
      <div style={{ position: 'absolute', left: left0, top: top0, width: mapW, opacity: open ? 0.15 : 1, filter: open ? 'blur(6px)' : 'blur(0px)',
        transform: plan ? `translate(${tx}px,${ty}px) scale(${sc})` : open ? 'scale(1.06)' : 'scale(1)', transformOrigin: plan ? '0 0' : '50% 50%',
        transition: 'opacity .5s, filter .5s, transform .8s cubic-bezier(.2,.8,.2,1)' }}>
        <svg viewBox="0 0 1376 768" style={{ width: '100%', display: 'block', overflow: 'visible' }}
          onMouseLeave={() => { setHov(null); setNear('') }}
          onMouseMove={e => {
            const r = e.currentTarget.getBoundingClientRect()
            const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height
            const nr = CONTINENTS.filter(c => Math.hypot((px - CONT_POS[c.id][0] / 100) * 1650, (py - CONT_POS[c.id][1] / 100) * 921) < 170).map(c => c.id).join(',')
            if (nr !== near) setNear(nr)
          }}>
          <rect x={-200} y={-200} width={1776} height={1168} fill="transparent" onMouseEnter={() => setHov(null)} />
          {(countries || []).map((c, i) => (
            <path key={c.n + i} d={c.d} fill={MAPCOL[c.c]} stroke="#f7f5f0" strokeWidth={0.6} strokeLinejoin="round"
              onMouseEnter={() => hov !== i && setHov(i)} onClick={() => pickCountry(i)} style={{ cursor: 'pointer' }} />
          ))}
          {hovC && !open && (
            <path key={hov} d={hovC.d} fill={MAPCOL[hovC.c]} stroke="#fffdf8" strokeWidth={1} strokeLinejoin="round"
              style={{ ['--s' as string]: hovC.s, transformBox: 'fill-box', transformOrigin: 'center', pointerEvents: 'none',
                filter: 'drop-shadow(0 6px 6px rgba(36,35,31,.35))', animation: 'countryPop .28s cubic-bezier(.3,1.6,.5,1) forwards' } as CSSProperties} />
          )}
          {showRoute && legs.map((l, i) => (
            <path key={i} d={l.path} fill="none" stroke={INK} strokeWidth={2} strokeDasharray="1 6" strokeLinecap="round"
              vectorEffect="non-scaling-stroke" style={{ pointerEvents: 'none' }} />
          ))}
        </svg>
        {!plan && CONTINENTS.map(c => {
          const hide = open || near.split(',').includes(c.id)
          return (
            <button key={c.id} className="hb-pill" onClick={() => { setHov(null); openSearch(); setQ(c.name) }}
              style={{ position: 'absolute', left: CONT_POS[c.id][0] + '%', top: CONT_POS[c.id][1] + '%', transform: `translate(-50%,-50%) scale(${hide ? 0.85 : 1})`,
                opacity: hide ? 0 : 1, pointerEvents: hide ? 'none' : 'auto', border: 'none', padding: '8px 14px', borderRadius: 999, fontSize: 13, fontWeight: 500,
                whiteSpace: 'nowrap', boxShadow: '0 2px 8px rgba(0,0,0,.12)', background: CARD, color: INK, transition: 'opacity .25s, transform .2s cubic-bezier(.2,.8,.2,1)' }}>
              {c.name}
            </button>
          )
        })}
        {hovC && !open && !plan && (
          <div style={{ position: 'absolute', left: hovC.x + '%', top: hovC.y + '%', transform: 'translate(-50%,calc(-100% - 16px))', ...tipStyle }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: MAPCOL[hovC.c] }} />{hovC.n}
          </div>
        )}
      </div>

      {/* destination results (search open) */}
      <div style={{ position: 'absolute', left: 40, right: 40, top: 190, bottom: 0, zIndex: 2, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 24,
        paddingBottom: 110, opacity: open ? 1 : 0, transform: `translateY(${open ? 0 : 40}px)`, pointerEvents: open ? 'auto' : 'none',
        transition: 'opacity .4s .15s, transform .7s .15s cubic-bezier(.2,.8,.2,1)' }}>
        <div data-keep="1" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px 32px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {MOODS.map(m => (
              <button key={m} onClick={() => setMood(m)} style={{ border: 'none', borderRadius: 999, padding: '8px 16px', fontSize: 14,
                background: m === mood ? '#c2714e' : '#ebe6dc', color: m === mood ? '#fff' : INK }}>{m}</button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {SEASONS.map(m => (
              <button key={m} onClick={() => setSeason(m)} style={{ border: 'none', borderRadius: 999, padding: '8px 16px', fontSize: 14,
                background: m === season ? INK : '#f2eee6', color: m === season ? '#fff' : INK }}>{m}</button>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', borderTop: '1px solid #d8d3c8', paddingTop: 20 }}>
          <div style={{ font: `700 36px ${FONT}`, letterSpacing: '-.03em' }}>{plural(res.length, 'place')}</div>
          <div style={{ font: `500 14px ${FONT}`, color: MUTED }}>{P.from ? [P.from, P.to].filter(Boolean).map(fmtDay).join(' – ') : season} · {plural(P.guests, 'traveller')}</div>
        </div>
        {dest && res.length === 0 && <div style={{ fontSize: 16, color: MUTED }}>No places match. Try a country or continent.</div>}
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${W < 760 ? 1 : W < 1100 ? 2 : 3},minmax(0,1fr))`, gap: 24 }}>
          {res.slice(0, shown).map((d, i) => {
            const on = has(d.name), img = open ? imageFor(d) : ''
            return (
              <div key={d.id} data-keep="1" style={{ display: 'flex', flexDirection: 'column', gap: 10, cursor: 'pointer', opacity: open ? 1 : 0,
                transform: `translateY(${open ? 0 : 24}px)`, transition: `opacity .5s ${open ? (0.25 + Math.min(i, 9) * 0.05).toFixed(2) : 0}s, transform .6s cubic-bezier(.2,.8,.2,1)` }}>
                <div className="hb-card-img" style={{ position: 'relative', height: 170, borderRadius: 6, overflow: 'hidden', background: '#e4dfd4' }}>
                  {img && <img src={img} alt={d.name} loading="lazy" referrerPolicy="no-referrer" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />}
                  <span style={{ position: 'absolute', left: 10, top: 10, display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(255,253,248,.92)', padding: '4px 10px', borderRadius: 999, fontSize: 12, fontWeight: 600, color: INK }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: CONTINENTS.find(c => c.id === d.c)?.color }} />{d.mood}
                  </span>
                  <button className="hb-grow" onClick={e => { e.stopPropagation(); const p = destPt(d); if (p) toggleStop(p) }}
                    style={{ position: 'absolute', right: 10, top: 10, border: 'none', borderRadius: 999, padding: '6px 12px', fontSize: 13, fontWeight: 600,
                      background: on ? INK : 'rgba(255,253,248,.94)', color: on ? '#fff' : INK, boxShadow: '0 2px 8px rgba(0,0,0,.12)', transition: 'background .2s, color .2s' }}>
                    {on ? '✓ Added' : '+ Add'}
                  </button>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                  <div style={{ fontSize: 18, fontWeight: 500 }}>{d.name}<span style={{ color: MUTED, fontWeight: 400 }}>, {d.country}</span></div>
                  <div style={{ font: `500 14px ${FONT}`, color: MUTED, whiteSpace: 'nowrap' }}>{d.season}</div>
                </div>
              </div>
            )
          })}
        </div>
        {res.length > shown && (
          <button data-keep="1" onClick={() => setShown(s => s + PAGE_SIZE)} style={{ alignSelf: 'center', border: `1px solid ${INK}`, background: 'transparent', borderRadius: 999, padding: '10px 22px', fontSize: 14, fontWeight: 600 }}>
            Show more places
          </button>
        )}
      </div>

      {/* route pins */}
      {pins.map(({ p, i, X, Y, crowded }) => (
        <div key={p.name + i} style={{ position: 'absolute', left: X, top: Y, zIndex: 3, pointerEvents: 'none', display: 'flex', alignItems: 'center', gap: 6, transform: 'translate(-13px,-13px)' }}>
          <span style={{ width: 26, height: 26, boxSizing: 'border-box', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700,
            background: i ? INK : CARD, color: i ? '#fff' : INK, border: i ? '2px solid #fffdf8' : `3px solid ${INK}`, boxShadow: '0 2px 6px rgba(36,35,31,.25)' }}>{i ? i : ''}</span>
          <span style={{ display: crowded ? 'none' : 'block', background: CARD, padding: '4px 10px', borderRadius: 999, fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap', boxShadow: '0 2px 8px rgba(0,0,0,.12)' }}>{p.name}</span>
        </div>
      ))}
      {hovPlan && hovC && (
        <div style={{ position: 'absolute', zIndex: 4, left: hovPlan[0], top: hovPlan[1], transform: 'translate(-50%,calc(-100% - 16px))', ...tipStyle }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: MAPCOL[hovC.c] }} />
          {has(hovC.n) ? hovC.n + ' · in your trip' : `+ Add ${hovC.n} as stop ${n + 1}`}
        </div>
      )}

      {/* tray (search open) */}
      {open && (
        <div data-keep="1" style={{ position: 'absolute', left: '50%', bottom: 24, transform: 'translateX(-50%)', zIndex: 6, display: 'flex', alignItems: 'center', gap: 8,
          maxWidth: Math.min(1100, W - 48), background: INK, color: '#f7f5f0', borderRadius: 999, padding: '8px 8px 8px 20px', boxShadow: '0 16px 40px rgba(36,35,31,.3)' }}>
          <span style={{ fontSize: 14, color: '#cfc9bd', whiteSpace: 'nowrap' }}>From <strong style={{ color: '#f7f5f0', fontWeight: 600 }}>{O.name}</strong></span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, overflowX: 'auto', scrollbarWidth: 'none', maxWidth: 640 }}>
            {P.stops.map(s => (
              <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                <span style={{ color: '#8d877b' }}>→</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, background: '#3a3832', borderRadius: 999, padding: '5px 6px 5px 12px', fontSize: 14, fontWeight: 500, whiteSpace: 'nowrap' }}>
                  {s.name}
                  <button className="hb-tray-x" onClick={() => removeStop(s.name)} aria-label="Remove" style={{ width: 22, height: 22, borderRadius: '50%', border: 'none', background: 'transparent', color: '#cfc9bd', fontSize: 14, lineHeight: 1 }}>×</button>
                </span>
              </div>
            ))}
          </div>
          <span style={{ fontSize: 14, color: '#cfc9bd', whiteSpace: 'nowrap', padding: '0 8px' }}>{n ? plural(n, 'stop') : 'Add places with + to build your route'}</span>
          <button className="hb-light" onClick={openPlan} style={{ border: 'none', background: '#f7f5f0', color: INK, borderRadius: 999, height: 42, padding: '0 20px', fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap' }}>Plan route →</button>
        </div>
      )}

      {/* planner */}
      {plan && (
        <aside ref={asideRef} data-keep="1" style={{ position: 'absolute', left: 24, top: 84, bottom: 24, width: Math.min(420, W - 48), zIndex: 5, background: CARD, borderRadius: 22, boxShadow: '0 16px 48px rgba(40,30,20,.16)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '20px 24px 14px', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>Step {step} of 3</span>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-.03em', whiteSpace: 'nowrap' }}>{stepTitle}</div>
            </div>
            <button onClick={step === 1 ? browse : back} style={linkBtn}>{step === 1 ? 'Browse ideas' : step === 3 ? '← Basics' : '← Edit route'}</button>
          </div>
          <div style={{ display: 'flex', gap: 4, padding: '0 24px 12px' }}>
            {[1, 2, 3].map(s => <span key={s} style={{ flex: 1, height: 3, borderRadius: 999, background: step >= s ? INK : '#e4dfd4', transition: 'background .3s' }} />)}
          </div>

          {step === 1 && (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 24px 16px', display: 'flex', flexDirection: 'column' }}>
                <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ width: 28, height: 28, boxSizing: 'border-box', flexShrink: 0, borderRadius: '50%', border: `3px solid ${INK}`, background: CARD }} />
                  <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>From</span>
                    <input value={oq} placeholder={O.name} onChange={e => { setOq(e.target.value); setOsugOpen(true); geoSearch(e.target.value) }}
                      onFocus={() => setOsugOpen(true)} onBlur={() => setTimeout(() => setOsugOpen(false), 150)}
                      style={{ border: 'none', outline: 'none', background: 'transparent', padding: 0, font: `600 17px ${FONT}`, color: INK, minWidth: 0 }} />
                  </div>
                  {osugOpen && (
                    <Dropdown title={oqn ? (oLive ? 'Matches' : geoDown ? 'Offline list (backend not reachable)' : 'Searching…') : 'Popular starting points'}>
                      {osug.map(g => (
                        <button key={g.name + g.country} className="hb-row" onMouseDown={e => { e.preventDefault(); set({ origin: g }); setOq(''); setOsugOpen(false) }} style={rowBtn}>
                          <span style={{ ...dot, background: MAPCOL[g.c || ''] || '#9a958a' }} />
                          <span style={{ fontSize: 15, fontWeight: 600, color: INK }}>{g.name} <span style={{ fontWeight: 400, color: MUTED }}>{g.country}</span></span>
                        </button>
                      ))}
                    </Dropdown>
                  )}
                </div>
                {P.stops.map((s, i) => (
                  <div key={s.name} data-stop="1" style={{ position: 'relative', display: 'flex', flexDirection: 'column' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 28 }}>
                      <span style={{ width: 28, alignSelf: 'stretch', display: 'flex', justifyContent: 'center' }}><span style={{ borderLeft: '2px dotted #b9b2a4' }} /></span>
                    </div>
                    <div onPointerDown={pDown(i)} onPointerMove={pMove(i)} onPointerUp={pUp(i)} onPointerCancel={pUp(i)}
                      style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 10, padding: '4px 6px 4px 0', marginRight: -6, background: CARD, borderRadius: 14,
                        touchAction: 'none', userSelect: 'none', ...rowMotion(i) }}>
                      <span style={{ width: 28, height: 28, flexShrink: 0, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, background: INK, color: '#fff' }}>{i + 1}</span>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <span style={ellipsis(17, 600)}>{s.name}</span>
                        <span style={{ ...ellipsis(13, 400), color: MUTED }}>{s.country}</span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                        <button className="hb-step" aria-label="Fewer nights" onClick={() => setStops(st => { st[i] = { ...st[i], nights: Math.max(1, nightsOf(st[i]) - 1) }; return st })} style={stepBtn}>−</button>
                        <span style={{ fontSize: 14, fontWeight: 500, minWidth: 62, textAlign: 'center', color: s.nights ? INK : MUTED, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{plural(nightsOf(s), 'night')}</span>
                        <button className="hb-step" aria-label="More nights" onClick={() => setStops(st => { st[i] = { ...st[i], nights: Math.min(60, nightsOf(st[i]) + 1) }; return st })} style={stepBtn}>+</button>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <span title="Drag to reorder" style={{ width: 22, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, letterSpacing: -2, color: '#b9b2a4', cursor: 'grab' }}>⋮⋮</span>
                        <button className="hb-x" aria-label="Remove" onClick={() => setStops(st => st.filter((_, j) => j !== i))} style={{ width: 26, height: 26, borderRadius: '50%', border: 'none', background: 'transparent', fontSize: 16, color: MUTED }}>×</button>
                      </div>
                    </div>
                  </div>
                ))}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 40 }}>
                  <span style={{ width: 28, alignSelf: 'stretch', display: 'flex', justifyContent: 'center' }}><span style={{ borderLeft: '2px dotted #d8d3c8' }} /></span>
                </div>
                <div style={{ position: 'relative' }}>
                  <input value={sq} placeholder="+ Add a stop, or click the map" onChange={e => { setSq(e.target.value); setSugOpen(true); geoSearch(e.target.value) }}
                    onFocus={() => setSugOpen(true)} onBlur={() => setTimeout(() => setSugOpen(false), 150)}
                    style={{ width: '100%', boxSizing: 'border-box', border: '1.5px dashed #c9c2b4', borderRadius: 14, padding: '13px 16px', background: 'transparent', outline: 'none', font: `500 15px ${FONT}`, color: INK }} />
                  {sugOpen && (
                    <Dropdown title={sqn ? (sqLive || sqn.length < 2 ? 'Matches' : geoDown ? 'Matches · offline list (backend not reachable)' : 'Searching…') : 'Near ' + last.name}>
                      {sug.map(g => (
                        <button key={g.name + g.country} className="hb-row" onMouseDown={e => { e.preventDefault(); add(g) }} style={rowBtn}>
                          <span style={{ ...dot, background: MAPCOL[g.c || ''] || '#9a958a' }} />
                          <span style={{ flex: 1, fontSize: 15, fontWeight: 600, color: INK }}>{g.name} <span style={{ fontWeight: 400, color: MUTED }}>{g.country}</span></span>
                          <span style={{ fontSize: 13, color: MUTED, whiteSpace: 'nowrap' }}>{fmtKm(km(last, g))}</span>
                        </button>
                      ))}
                    </Dropdown>
                  )}
                </div>
                <button onClick={() => set({ back: !P.back })} style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between', border: 'none', background: 'transparent', padding: 0, fontSize: 15, fontWeight: 500, color: INK }}>
                  <span>Return to {O.name}</span>
                  <span style={{ position: 'relative', width: 38, height: 22, borderRadius: 999, background: P.back ? INK : '#d8d3c8', transition: 'background .2s' }}>
                    <span style={{ position: 'absolute', top: 2, left: P.back ? 18 : 2, width: 18, height: 18, borderRadius: '50%', background: CARD, transition: 'left .2s' }} />
                  </span>
                </button>
              </div>
              <Footer>
                <button className="hb-cta" onClick={next} style={{ ...ctaBtn, background: n ? INK : disabled, cursor: n ? 'pointer' : 'default' }}>
                  <span>Continue</span><span style={ctaSub}>{n ? `${plural(n, 'stop')} · ${total} nights` : 'Add at least one stop'} →</span>
                </button>
              </Footer>
            </div>
          )}

          {step === 2 && (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 24px 16px', display: 'flex', flexDirection: 'column', gap: 18 }}>
                <button onClick={back} style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 3, border: 'none', background: 'transparent', padding: 0, textAlign: 'left' }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>Route</span>
                  <span style={{ fontSize: 14, fontWeight: 600, color: INK, lineHeight: 1.45 }}>{[O.name, ...P.stops.map(x => x.name), ...(P.back && n ? [O.name] : [])].join(' → ')}</span>
                </button>
                <div style={{ display: 'flex', flexDirection: 'column', borderTop: `1px solid ${LINE}` }}>
                  <Row label="When" sub={<button onClick={() => { setPlan(false); openSearch(); setCal(true) }} style={{ ...linkBtn, fontSize: 13, color: MUTED, padding: 0 }}>{dated ? 'Change dates' : 'Exact dates'}</button>}>
                    {dated ? <span style={{ fontSize: 15, fontWeight: 600, textAlign: 'right' }}>{dateText}</span>
                      : <Counter value={`${P.days} days`} minus={() => set({ days: Math.max(2, P.days - 1) })} plus={() => set({ days: Math.min(90, P.days + 1) })} label="days" />}
                  </Row>
                  <Row label="Travellers">
                    <Counter value={String(P.guests)} minus={() => set({ guests: Math.max(1, P.guests - 1) })} plus={() => set({ guests: Math.min(12, P.guests + 1) })} label="travellers" />
                  </Row>
                  <Row label="Budget" sub={<span style={{ fontSize: 13, color: MUTED }}>For the whole group</span>}>
                    <label style={{ display: 'flex', alignItems: 'baseline', gap: 6, borderBottom: `1.5px solid ${INK}`, paddingBottom: 3 }}>
                      <span style={{ fontSize: 14, color: MUTED }}>CHF</span>
                      <input value={String(P.budget)} inputMode="numeric" aria-label="Budget in CHF" onChange={e => set({ budget: Math.max(0, parseInt(e.target.value.replace(/\D/g, '')) || 0) })}
                        style={{ width: 72, border: 'none', outline: 'none', background: 'transparent', padding: 0, textAlign: 'right', font: `600 17px ${FONT}`, color: INK, fontVariantNumeric: 'tabular-nums' }} />
                    </label>
                  </Row>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '16px 0 4px' }}>
                    <span style={{ fontSize: 15, fontWeight: 600, paddingBottom: 8 }}>Class</span>
                    {TIERS.map(([id, label, hint]) => {
                      const on = P.tier === id
                      return (
                        <button key={id} onClick={() => set({ tier: id })} aria-pressed={on} style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 44, padding: 0, border: 'none', background: 'transparent', textAlign: 'left', color: INK }}>
                          <span style={{ width: 18, height: 18, boxSizing: 'border-box', flexShrink: 0, borderRadius: '50%', border: on ? `5.5px solid ${INK}` : '1.5px solid #b9b2a4', transition: 'border .15s' }} />
                          <span style={{ flex: 1, fontSize: 15, fontWeight: on ? 700 : 500 }}>{label}</span>
                          <span style={{ fontSize: 13, color: MUTED }}>{hint}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
              <Footer>
                <button className="hb-cta" onClick={next} style={{ ...ctaBtn, background: INK }}><span>Continue</span><span style={ctaSub}>Preferences →</span></button>
              </Footer>
            </div>
          )}

          {step === 3 && (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 24px 16px', display: 'flex', flexDirection: 'column', gap: 22 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <Heading title="What kind of trip?" sub="Pick one" />
                  <div style={grid2}>
                    {TRIP_TYPES.map(([id, label]) => {
                      const on = P.tripType === id
                      return (
                        <button key={id} className="hb-chip" onClick={() => set({ tripType: id })} aria-pressed={on} style={chip(on)}>
                          <span style={{ width: 16, height: 16, boxSizing: 'border-box', flexShrink: 0, borderRadius: '50%', border: on ? `5px solid ${INK}` : '1.5px solid #b9b2a4', transition: 'border .15s' }} />
                          <span style={{ lineHeight: 1.2, textWrap: 'balance' } as CSSProperties}>{label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 }}>
                    <Heading title="What would you like to see?" sub="Pick as many as you like" />
                    <span style={{ fontSize: 12, fontWeight: 600, color: MUTED, whiteSpace: 'nowrap' }}>{P.interests.length ? P.interests.length + ' selected' : ''}</span>
                  </div>
                  <div style={grid2}>
                    {INTERESTS.map(l => {
                      const on = P.interests.includes(l)
                      return (
                        <button key={l} className="hb-chip" aria-pressed={on} onClick={() => set({ interests: on ? P.interests.filter(x => x !== l) : [...P.interests, l] })} style={chip(on)}>
                          <span style={{ width: 16, height: 16, boxSizing: 'border-box', flexShrink: 0, borderRadius: 5, border: `1.5px solid ${on ? INK : '#b9b2a4'}`, background: on ? INK : CARD, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'background .15s' }}>
                            <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" style={{ opacity: on ? 1 : 0 }}><path d="M2.5 6.2l2.2 2.2 4.8-4.8" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
                          </span>
                          <span style={{ lineHeight: 1.2, textWrap: 'balance' } as CSSProperties}>{l}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
                {fixed > total && <div style={{ fontSize: 13, color: '#a8432a' }}>Fixed nights ({fixed}) are more than the trip length ({total}).</div>}
                {send && <div style={{ fontSize: 13, color: send.state === 'error' ? '#a8432a' : '#4a4740' }}>{send.msg}</div>}
              </div>
              <Footer>
                <button className="hb-cta" onClick={submit} style={{ ...ctaBtn, background: n && !(fixed > total) ? INK : disabled }}>
                  <span>{send?.state === 'sending' ? 'Planning…' : 'Plan my trip'}</span>
                  <span style={ctaSub}>{total} nights total{fixed ? ` · ${fixed} fixed` : ''} →</span>
                </button>
              </Footer>
            </div>
          )}
        </aside>
      )}

      {/* search bar */}
      <div data-keep="1" style={{ position: 'absolute', left: '50%', zIndex: 4, top: barTop, width: barW, transform: 'translateX(-50%)',
        transition: 'top .75s cubic-bezier(.2,.8,.2,1), width .75s cubic-bezier(.2,.8,.2,1)', display: 'flex', alignItems: 'stretch', gap: 4,
        background: CARD, borderRadius: open ? 20 : 999, padding: 8, boxSizing: 'border-box', boxShadow: '0 12px 40px rgba(40,30,20,.16)' }}>
        <label style={{ flex: 1.4, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 4, padding: open ? '10px 18px' : '0 20px', cursor: 'text' }}>
          {open && <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>Where to?</span>}
          <input value={q} placeholder={open ? 'Anywhere' : 'Where do you want to go?'}
            onChange={e => { setQ(e.target.value); openSearch(); setWf(true); geoSearch(e.target.value) }}
            onFocus={() => { setWf(true); openSearch() }} onBlur={() => setTimeout(() => setWf(false), 150)} onClick={openSearch}
            style={{ border: 'none', outline: 'none', background: 'transparent', fontSize: 18, fontWeight: open ? 500 : 400, color: INK, padding: 0, minWidth: 0, fontFamily: FONT }} />
        </label>
        {open && wf && whereSug.length > 0 && (
          <div style={{ position: 'absolute', top: 'calc(100% + 10px)', left: 8, width: Math.min(460, barW - 16), zIndex: 12, background: CARD, borderRadius: 20, padding: 10, boxShadow: '0 24px 60px rgba(40,30,20,.22)', display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={ddTitle}>Places</div>
            {whereSug.map(g => {
              const on = has(g.name)
              return (
                <button key={g.name + g.lat} className="hb-row2" onMouseDown={e => { e.preventDefault(); if (on) removeStop(g.name); else add(fromGeo(g)) }} style={{ ...rowBtn, gap: 12 }}>
                  <span style={{ ...dot, background: MAPCOL[contOf(g.country || '') || ''] || '#9a958a' }} />
                  <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
                    <span style={{ fontSize: 16, fontWeight: 600, color: INK }}>{g.name}</span>
                    <span style={{ ...ellipsis(13, 400), color: MUTED }}>{[g.state, g.country].filter(Boolean).join(', ')}</span>
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 600, padding: '6px 12px', borderRadius: 999, whiteSpace: 'nowrap', background: on ? INK : '#f1ece2', color: on ? '#fff' : INK }}>{on ? '✓ In trip' : '+ Add to trip'}</span>
                </button>
              )
            })}
          </div>
        )}
        {open && (
          <>
            <div data-keep="1" style={{ position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '6px 8px', borderLeft: `1px solid ${LINE}` }}>
              <button className="hb-calbtn" onClick={() => setCal(c => !c)} style={{ border: 'none', background: cal ? '#f1ece2' : 'transparent', borderRadius: 14, padding: '6px 12px', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 6, transition: 'background .2s' }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>When?</span>
                <span style={{ fontSize: 17, fontWeight: 500, color: P.from ? INK : '#9a958a', whiteSpace: 'nowrap' }}>{dateText}</span>
              </button>
              {cal && (
                <div style={{ position: 'absolute', top: 'calc(100% + 18px)', left: '50%', transform: 'translateX(-50%)', zIndex: 10, width: Math.min(640, W - 48), background: CARD, borderRadius: 24, padding: '24px 28px', boxShadow: '0 24px 60px rgba(40,30,20,.22)', display: 'flex', flexDirection: 'column', gap: 20 }}>
                  <div style={{ position: 'relative', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 36 }}>
                    <button className="hb-round" aria-label="Previous month" onClick={() => setCalM(Math.max(curM, baseM - 1))} style={{ ...navBtn, left: -6, opacity: baseM > curM ? 1 : 0.3 }}>‹</button>
                    <button className="hb-round" aria-label="Next month" onClick={() => setCalM(baseM + 1)} style={{ ...navBtn, right: -6 }}>›</button>
                    {[0, 1].map(o => {
                      const ym = baseM + o, y = Math.floor(ym / 12), m = ym % 12
                      const lead = (new Date(y, m, 1).getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate()
                      return (
                        <div key={o} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                          <div style={{ textAlign: 'center', fontSize: 16, fontWeight: 700, lineHeight: '24px' }}>{MONTHS[m]} {y}</div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', rowGap: 2 }}>
                            {['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map(w => <div key={w} style={{ textAlign: 'center', fontSize: 12, fontWeight: 600, color: MUTED, paddingBottom: 6 }}>{w}</div>)}
                            {Array.from({ length: lead }, (_, i) => <div key={'l' + i} />)}
                            {Array.from({ length: days }, (_, i) => {
                              const d = iso(y, m, i + 1), past = d < today, end = d === P.from || d === P.to, inR = P.from && P.to && d > P.from && d < P.to
                              return (
                                <div key={d} style={{ display: 'flex', justifyContent: 'center', background: inR || (P.from && P.to && end) ? '#efe7d8' : 'transparent',
                                  borderRadius: P.from && P.to && d === P.from ? '50% 0 0 50%' : P.from && P.to && d === P.to ? '0 50% 50% 0' : 0 }}>
                                  <button className="hb-day" disabled={past} onClick={() => pickDay(d)} style={{ width: 38, height: 38, borderRadius: '50%', border: 'none', background: end ? INK : 'transparent',
                                    color: end ? '#fff' : INK, font: `${d === today ? 700 : 500} 14px ${FONT}`, cursor: past ? 'default' : 'pointer', opacity: past ? 0.35 : 1 }}>{i + 1}</button>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: `1px solid ${LINE}`, paddingTop: 16 }}>
                    <span style={{ fontSize: 14, color: MUTED }}>{calNights ? `${plural(calNights, 'night')} · ${fmtDay(P.from)} – ${fmtDay(P.to)}` : P.from ? 'Select your return date' : 'Select your departure date'}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                      <button onClick={() => set({ from: '', to: '' })} style={{ ...linkBtn, padding: 0 }}>Clear</button>
                      <button className="hb-accent" onClick={() => setCal(false)} style={{ border: 'none', background: INK, color: '#f7f4ed', borderRadius: 999, padding: '10px 22px', fontSize: 14, fontWeight: 600 }}>Done</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '10px 18px', borderLeft: `1px solid ${LINE}` }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: MUTED }}>Travellers</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button onClick={() => set({ guests: Math.max(1, P.guests - 1) })} style={roundSmall}>−</button>
                <span style={{ fontSize: 17, fontWeight: 500, minWidth: 14, textAlign: 'center' }}>{P.guests}</span>
                <button onClick={() => set({ guests: Math.min(12, P.guests + 1) })} style={roundSmall}>+</button>
              </div>
            </div>
          </>
        )}
        <button className="hb-accent" onClick={open ? openPlan : openSearch} style={{ border: 'none', background: INK, color: '#f7f5f0', borderRadius: open ? 14 : 999, height: open ? 60 : 48,
          alignSelf: 'center', padding: `0 ${open ? 30 : 26}px`, fontSize: 15, fontWeight: open ? 600 : 500, transition: 'background .2s', whiteSpace: 'nowrap' }}>
          {open ? (n ? 'Plan route' : 'Find trips') : 'Search'}
        </button>
      </div>
    </div>
  )
}

// --- small building blocks --------------------------------------------------------------------------------

const tipStyle: CSSProperties = { pointerEvents: 'none', display: 'flex', alignItems: 'center', gap: 8, background: INK, color: '#f7f5f0', padding: '8px 14px', borderRadius: 999,
  fontSize: 14, fontWeight: 500, whiteSpace: 'nowrap', boxShadow: '0 8px 20px rgba(36,35,31,.22)' }
const linkBtn: CSSProperties = { flexShrink: 0, whiteSpace: 'nowrap', border: 'none', background: 'transparent', padding: '0 0 4px', fontSize: 14, fontWeight: 500, color: INK,
  textDecoration: 'underline', textUnderlineOffset: 3 }
const rowBtn: CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, border: 'none', background: 'transparent', borderRadius: 12, padding: 10, textAlign: 'left' }
const dot: CSSProperties = { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 }
const ddTitle: CSSProperties = { fontSize: 12, fontWeight: 700, color: MUTED, padding: '6px 10px' }
const stepBtn: CSSProperties = { width: 24, height: 24, border: 'none', borderRadius: 6, background: 'transparent', padding: 0, fontSize: 17, fontWeight: 400, lineHeight: 1, color: '#9a958a', transition: 'color .15s, background .15s' }
const ctaBtn: CSSProperties = { border: 'none', color: '#f7f5f0', borderRadius: 14, height: 54, padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, fontSize: 16, fontWeight: 600, whiteSpace: 'nowrap', width: '100%' }
const ctaSub: CSSProperties = { fontSize: 13, fontWeight: 500, opacity: 0.8 }
const grid2: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 6 }
const navBtn: CSSProperties = { position: 'absolute', top: -4, width: 32, height: 32, borderRadius: '50%', border: 'none', background: 'transparent', fontSize: 18, color: INK }
const roundSmall: CSSProperties = { width: 28, height: 28, borderRadius: '50%', border: '1px solid #d8d3c8', background: CARD, fontSize: 15, lineHeight: 1 }
const ellipsis = (size: number, weight: number): CSSProperties => ({ fontSize: size, fontWeight: weight, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' })
const chip = (on: boolean): CSSProperties => ({ display: 'flex', alignItems: 'center', gap: 9, minHeight: 44, padding: '6px 10px', border: 'none', borderRadius: 12,
  background: on ? CARD : '#f7f4ee', boxShadow: on ? `inset 0 0 0 2px ${INK}` : 'none', color: INK, fontSize: 14, fontWeight: 600, textAlign: 'left', minWidth: 0, transition: 'background .15s, box-shadow .15s' })

function Dropdown({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ position: 'absolute', top: 'calc(100% + 8px)', left: 0, right: 0, zIndex: 3, background: CARD, borderRadius: 18, padding: 8, boxShadow: '0 24px 60px rgba(40,30,20,.22)', display: 'flex', flexDirection: 'column' }}>
      <div style={ddTitle}>{title}</div>
      {children}
    </div>
  )
}

function Footer({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '16px 24px 20px', borderTop: `1px solid ${LINE}`, display: 'flex', flexDirection: 'column', gap: 10 }}>{children}</div>
}

function Row({ label, sub, children }: { label: string; sub?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, minHeight: 64, borderBottom: `1px solid ${LINE}` }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, alignItems: 'flex-start' }}><span style={{ fontSize: 15, fontWeight: 600 }}>{label}</span>{sub}</div>
      {children}
    </div>
  )
}

function Counter({ value, minus, plus, label }: { value: string; minus: () => void; plus: () => void; label: string }) {
  const b: CSSProperties = { width: 30, height: 30, borderRadius: '50%', border: '1px solid #d8d3c8', background: 'transparent', fontSize: 15, lineHeight: 1, color: INK }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <button className="hb-ring" aria-label={`Fewer ${label}`} onClick={minus} style={b}>−</button>
      <span style={{ fontSize: 15, fontWeight: 600, minWidth: 64, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{value}</span>
      <button className="hb-ring" aria-label={`More ${label}`} onClick={plus} style={b}>+</button>
    </div>
  )
}

function Heading({ title, sub }: { title: string; sub: string }) {
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}><span style={{ fontSize: 15, fontWeight: 700 }}>{title}</span><span style={{ fontSize: 13, color: MUTED }}>{sub}</span></div>
}
