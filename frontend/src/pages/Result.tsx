// Trip results (design 8c "Photo header"): photo band with editable title and total, the route as legs and
// stop cards, the cost panel, and a modal per stop. Every change goes to POST /api/trips/{id}/adjust and the
// page shows the BudgetReport it returns (prices are computed by the backend, never here).
// Ported from design_handoff_holydays/Holydays Trip Results.dc.html.
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api, type AdjustBody, type LegChoice, type PriceRange, type Tier, type Trip } from '../api'
import { imageFor, useImageVersion } from '../images'
import { CONT_NAME, loadCountries, useDestinations, type Country } from '../mapdata'

const FONT = "'Bricolage Grotesque', sans-serif"
const BODY = "'DM Sans', sans-serif"
const INK = '#24231f', MUTED = '#6b675e', CARD = '#fffdf8', LINE = '#ece6da'
const CAT: Record<string, string> = { transport: 'Transport', accommodation: 'Stays', food: 'Food', activities: 'Activities', local_transport: 'Getting around' }
const CATCOL: Record<string, string> = { transport: '#24231f', accommodation: '#bc7654', food: '#c8af83', activities: '#8a8cb2', local_transport: '#979e75' }
const MODE: Record<string, string> = { flight: 'Flight', train: 'Train', bus: 'Bus', ferry: 'Ferry', car: 'Car', other: 'Other' }
const ICON: Record<string, string> = {
  flight: 'M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z',
  train: 'M12 2c-4 0-8 .5-8 4v9.5A3.5 3.5 0 0 0 7.5 19L6 20.5v.5h2l2-2h4l2 2h2v-.5L16.5 19a3.5 3.5 0 0 0 3.5-3.5V6c0-3.5-4-4-8-4zM7.5 17a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zM11 10H6V6h5v4zm2 0V6h5v4h-5zm3.5 7a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z',
  bus: 'M4 16c0 .88.39 1.67 1 2.22V20a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h8v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm9 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zM18 11H6V6h12v5z',
  ferry: 'M20 21c-1.4 0-2.8-.5-4-1.3-2.4 1.7-5.6 1.7-8 0C6.8 20.5 5.4 21 4 21H2v2h2c1.4 0 2.7-.3 4-1 2.5 1.3 5.5 1.3 8 0 1.3.7 2.6 1 4 1h2v-2h-2zM3.9 19H4c1.6 0 3-.9 4-2 1 1.1 2.4 2 4 2s3-.9 4-2c1 1.1 2.4 2 4 2h.1l1.9-6.7c.1-.3 0-.6-.1-.8-.2-.2-.4-.4-.7-.4L20 10.6V6c0-1.1-.9-2-2-2h-3V1H9v3H6c-1.1 0-2 .9-2 2v4.6l-1.3.5c-.3.1-.5.2-.6.5-.1.2-.2.5-.1.8L3.9 19zM6 6h12v4l-6-2-6 2V6z',
  car: 'M18.9 6c-.2-.6-.8-1-1.4-1h-11c-.7 0-1.2.4-1.4 1L3 12v8a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h12v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-8l-2.1-6zM6.5 16a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm11 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zM5 11l1.5-4.5h11L19 11H5z',
  other: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z',
}
const TIERS: [Tier, string][] = [['budget', 'Budget'], ['comfort', 'Comfort'], ['luxury', 'Luxury']]
const TAGS: ['fastest' | 'cheapest' | 'best_value', string, string, string][] = [
  ['fastest', 'Fastest', '#e6e3ef', '#4b4d78'], ['cheapest', 'Cheapest', '#e3eadb', '#3e5a2c'], ['best_value', 'Best value', '#f3e6d6', '#8a5a1c']]
const NO_CHANGES = { leg_choice: {}, accommodation_tier: {}, food_tier: {}, skipped_attractions: [], added_attractions: [], nights: {} }

const r10 = (v: number) => Math.round(v / 10) * 10
const fmtN = (v: number) => Math.round(v).toLocaleString('en-US')
const mid = (m: PriceRange) => (m.min + m.max) / 2
const fmtH = (h: number) => h < 1 ? Math.round(h * 60) + ' min' : Math.round(h * 10) / 10 + ' h'
const cap = (s: string) => s ? s[0].toUpperCase() + s.slice(1) : ''
const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`
const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u } }

function useWide(min: number) {
  const [wide, setWide] = useState(window.innerWidth >= min)
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= min)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [min])
  return wide
}

export default function Result({ trip, onTrip }: { trip: Trip; onTrip: (t: Trip) => void }) {
  const rep = trip.report!, res = trip.research!, req = trip.request
  const usd = trip.usd_rate || 1, cur = rep.currency, pax = req.travelers
  const tier = rep.selected_tier, B = rep.selected, adj = rep.adjustments
  const dest = useDestinations()
  const imgVersion = useImageVersion()
  const wide = useWide(1000)
  const [countries, setCountries] = useState<Country[] | null>(null)
  useEffect(() => { loadCountries().then(setCountries).catch(() => {}) }, [])
  const [openStop, setOpenStop] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'sight' | 'activity'>('all')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const adjust = async (body: AdjustBody) => {
    setBusy(true); setErr(null)
    try { onTrip({ ...trip, report: await api.adjust(trip.id, body) }) }
    catch (e) { setErr((e as Error).message) }
    finally { setBusy(false) }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenStop(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // --- title: 1-3 countries "China & Japan trip", more on one continent "Asia trip", else "Multi-country trip"
  const ctr = [...new Set(res.places.map(p => p.country).filter(Boolean))]
  const conts = [...new Set(ctr.map(n => (countries || []).find(k => k.n === n)?.c).filter(Boolean))] as string[]
  const autoTitle = ctr.length === 0 ? 'Your trip' : ctr.length === 1 ? ctr[0] + ' trip'
    : ctr.length <= 3 ? ctr.slice(0, -1).join(', ') + ' & ' + ctr[ctr.length - 1] + ' trip'
    : conts.length === 1 ? CONT_NAME[conts[0]] + ' trip' : 'Multi-country trip'
  const [titleIn, setTitleIn] = useState(trip.title ?? autoTitle)
  useEffect(() => setTitleIn(trip.title ?? autoTitle), [trip.title, autoTitle])
  const saveTitle = (v: string | null) => {
    const value = v && v.trim() && v.trim() !== autoTitle ? v.trim() : null
    if (value === (trip.title ?? null)) { if (!value) setTitleIn(autoTitle); return }
    api.patchTrip(trip.id, { title: value }).then(t => onTrip({ ...trip, title: t.title })).catch(e => setErr(e.message))
  }
  const titleEdited = !!trip.title && trip.title !== autoTitle

  // --- money helpers (the report is already in the trip currency; place prices are USD) ------------------
  const money = (m: PriceRange) => `${cur} ${fmtN(r10(m.min))} – ${fmtN(r10(m.max))}`
  const usdR = (p: PriceRange) => `${cur} ${fmtN(Math.round(p.min * usd))}` + (Math.round(p.max) !== Math.round(p.min) ? '–' + fmtN(Math.round(p.max * usd)) : '')
  const avgUsd = (p: PriceRange) => `${cur} ${fmtN(Math.round(mid(p) * usd))}`

  const names = [req.origin, ...req.stops.map(x => x.name), ...(req.return_to_origin ? [req.origin] : [])]
  const nightsTotal = res.nights.reduce((a, n) => a + n.nights, 0)
  const days = req.start_date && req.end_date ? Math.round((Date.parse(req.end_date) - Date.parse(req.start_date)) / 864e5) + 1 : req.days
  const fmtD = (k: string) => new Date(k + 'T12:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  const meta = [req.start_date && req.end_date ? fmtD(req.start_date) + ' – ' + fmtD(req.end_date) : days + ' days', nightsTotal + ' nights',
    plural(pax, 'traveller'), cap(req.trip_type)].join(' · ')
  const diff = mid(B.total) - rep.budget

  const photos = useMemo(() => {
    const m: Record<string, string> = {}
    const want = new Set(res.places.map(p => p.name))
    ;(dest || []).forEach(d => { if (!m[d.name] && want.has(d.name)) { const im = imageFor(d); if (im) m[d.name] = im } })
    return m
  }, [dest, res.places, imgVersion])
  const bg = (name: string) => photos[name] ? `url("${photos[name]}") center / cover` : '#cfc6b6'

  // --- attractions in the plan: day plan + added - skipped -------------------------------------------------
  const sched = new Set(res.days.flatMap(d => d.attraction_ids))
  const dayOf: Record<string, number> = {}
  res.days.forEach(d => d.attraction_ids.forEach(aid => { dayOf[aid] ??= d.day }))
  const skipped = new Set(adj.skipped_attractions), added = new Set(adj.added_attractions || [])
  const isOn = (aid: string) => (sched.has(aid) || added.has(aid)) && !skipped.has(aid)
  const toggleAtt = (aid: string) => {
    const s = new Set(skipped), a = new Set(added)
    if (isOn(aid)) { if (sched.has(aid)) s.add(aid); a.delete(aid) }
    else { s.delete(aid); if (!sched.has(aid)) a.add(aid) }
    adjust({ skipped_attractions: [...s], added_attractions: [...a] })
  }

  const nightsOf = (name: string) => adj.nights[name] ?? res.nights.find(x => x.place === name)?.nights ?? 0
  const stopCost = (name: string): PriceRange => B.by_place?.[name]
    ?? B.lines.filter(l => l.label.startsWith(name + ':')).reduce((a, l) => ({ min: a.min + l.total.min, max: a.max + l.total.max }), { min: 0, max: 0 })

  // --- route flow: leg, stop, leg, stop, ... ---------------------------------------------------------------
  const legView = (i: number) => {
    const L = res.legs[i], ch: LegChoice | undefined = B.leg_choices.find(c => c.leg_index === i)
    if (!L || !ch) return null
    const chosen = L.options[ch.chosen]
    return (
      <div key={'leg' + i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '8px 0' }}>
        <span style={{ height: 20, borderLeft: '2px dotted #b9b2a4', opacity: i === 0 ? 0 : 1 }} />
        <div style={{ width: '100%', maxWidth: 620, display: 'grid', gridAutoFlow: 'column', gridAutoColumns: 'minmax(0,1fr)', gap: 8 }}>
          {L.options.map((o, j) => {
            const on = j === ch.chosen
            return (
              <button key={j} className="hr-opt" disabled={busy} onClick={() => !on && adjust({ leg_choice: { ...adj.leg_choice, [i]: j } })} aria-pressed={on}
                title={`${MODE[o.mode] || o.mode}: ${o.description}`} aria-label={`${MODE[o.mode] || o.mode}, ${fmtH(o.duration_hours)}, ${usdR(o.price)} per person`}
                style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4, minWidth: 0, padding: '7px 30px 7px 10px', border: 'none', borderRadius: 12,
                  background: CARD, color: INK, boxShadow: on ? `inset 0 0 0 2px ${INK}` : 'inset 0 0 0 1px #e2dccf', textAlign: 'left', transition: 'box-shadow .2s', cursor: on ? 'default' : 'pointer' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, maxWidth: '100%', fontSize: 13, whiteSpace: 'nowrap' }}>
                  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" style={{ flexShrink: 0, fill: INK }}><path d={ICON[o.mode] || ICON.other} /></svg>
                  <span style={{ fontWeight: 700 }}>{fmtH(o.duration_hours)}</span>
                  <span style={{ color: MUTED, fontVariantNumeric: 'tabular-nums', overflow: 'hidden', textOverflow: 'ellipsis' }}>{usdR(o.price)}</span>
                </span>
                <span style={{ display: 'flex', gap: 4, minHeight: 16, flexWrap: 'wrap' }}>
                  {TAGS.filter(([key]) => ch[key] === j).map(([, label, b, f]) => (
                    <span key={label} style={{ fontSize: 10, fontWeight: 700, lineHeight: 1, padding: '3px 6px', borderRadius: 999, whiteSpace: 'nowrap', background: b, color: f }}>{label}</span>
                  ))}
                </span>
                <span style={{ position: 'absolute', top: 8, right: 9, width: 14, height: 14, boxSizing: 'border-box', borderRadius: '50%', border: `1.5px solid ${on ? INK : '#cfc9bd'}`,
                  background: on ? INK : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg viewBox="0 0 12 12" width="9" height="9" aria-hidden="true" style={{ opacity: on ? 1 : 0 }}><path d="M2.5 6.2l2.2 2.2 4.8-4.8" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
              </button>
            )
          })}
        </div>
        {chosen && (
          <span style={{ maxWidth: 620, fontSize: 12, color: MUTED, textAlign: 'center', fontFamily: BODY }}>
            {MODE[chosen.mode] || chosen.mode} · {chosen.description}
            {chosen.source_url && <> · <a href={chosen.source_url} target="_blank" rel="noreferrer" style={{ textDecoration: 'underline', textUnderlineOffset: 2 }}>source ↗</a></>}
          </span>
        )}
        <span style={{ height: 20, borderLeft: '2px dotted #b9b2a4', opacity: i === res.places.length ? 0 : 1 }} />
      </div>
    )
  }

  const stopCard = (i: number) => {
    const p = res.places[i], n = nightsOf(p.name)
    const at = adj.accommodation_tier[p.name] || tier, ft = adj.food_tier[p.name] || tier
    const open = openStop === p.name
    const cost = `${cur} ${fmtN(r10(mid(stopCost(p.name))))}`
    return (
      <div key={p.name} style={{ background: CARD, borderRadius: 22, overflow: 'hidden', boxShadow: open ? '0 10px 30px rgba(40,30,20,.12)' : '0 1px 3px rgba(40,30,20,.06)', transition: 'box-shadow .25s' }}>
        <div className="hr-stop" role="button" aria-haspopup="dialog" onClick={() => setOpenStop(p.name)}
          style={{ display: 'grid', gridTemplateColumns: wide ? '180px minmax(0,1fr) 210px' : '110px minmax(0,1fr)', cursor: 'pointer' }}>
          <div style={{ position: 'relative', minHeight: 150, background: '#cfc6b6' }}>
            <div role="img" aria-label={p.name} style={{ position: 'absolute', inset: 0, background: bg(p.name) }} />
            <span style={{ position: 'absolute', left: 14, top: 14, width: 30, height: 30, borderRadius: '50%', background: CARD, color: INK, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700 }}>{i + 1}</span>
          </div>
          <div style={{ padding: '20px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, minWidth: 0, flexWrap: wide ? 'nowrap' : 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
              <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-.03em', lineHeight: 1.05 }}>{p.name}</span>
              <span style={{ fontSize: 14, color: MUTED }}>{p.country} · {plural(n, 'night')}</span>
            </div>
            <div style={{ flexShrink: 0, width: 130, display: 'flex', flexDirection: 'column', gap: 7 }}>
              <Kv k="Stay" v={avgUsd(p.accommodation_per_night[at])} />
              <Kv k="Meal" v={avgUsd(p.meal_cost[ft])} />
              <Kv k="Local" v={avgUsd(p.local_transport_per_day[tier])} />
            </div>
            {!wide && <span style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{cost}</span>}
          </div>
          {wide && (
            <div style={{ padding: '20px 24px', borderLeft: `1px solid ${LINE}`, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>Total</span>
              <span style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-.035em', lineHeight: 1, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{cost}</span>
            </div>
          )}
        </div>
      </div>
    )
  }

  const flow: React.ReactNode[] = []
  res.legs.forEach((_, i) => { flow.push(legView(i)); if (res.places[i]) flow.push(stopCard(i)) })
  for (let i = res.legs.length; i < res.places.length; i++) flow.push(stopCard(i))

  const cats = Object.entries(B.by_category).filter(([, m]) => m.max > 0).sort((a, b) => mid(b[1]) - mid(a[1]))
  const changed = JSON.stringify({ ...NO_CHANGES, ...adj }) !== JSON.stringify(NO_CHANGES)

  const copyLink = () => {
    navigator.clipboard?.writeText(window.location.href).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500) }).catch(() => {})
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f7f5f0', fontFamily: FONT, color: INK }}>
      {/* photo header */}
      <div style={{ position: 'relative', height: 400, display: 'flex', gap: 3, background: INK }}>
        {res.places.map((p, i) => (
          <div key={p.name} role="img" aria-label={p.name} style={{ flex: 1, position: 'relative', background: bg(p.name) }}>
            <span style={{ position: 'absolute', left: 16, top: 84, background: 'rgba(255,253,248,.94)', color: INK, padding: '5px 11px', borderRadius: 999, fontSize: 13, fontWeight: 600 }}>{i + 1} · {p.name}</span>
          </div>
        ))}
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(rgba(20,18,15,.45),rgba(20,18,15,0) 26%,rgba(20,18,15,.4) 52%,rgba(20,18,15,.88))', pointerEvents: 'none' }} />
        <header style={{ position: 'absolute', top: 0, left: 0, right: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '22px 40px', color: '#fff' }}>
          <Link to="/" style={{ font: `800 28px ${FONT}`, letterSpacing: '-.03em', color: '#fff' }}>Holydays</Link>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Link to={`/plan?trip=${trip.id}`} style={{ border: '1px solid #fff', color: '#fff', padding: '9px 18px', borderRadius: 999, fontSize: 14 }}>Edit route</Link>
            <button onClick={copyLink} style={{ border: 'none', background: '#fff', color: INK, padding: '10px 18px', borderRadius: 999, fontSize: 14, fontWeight: 600 }}>{copied ? 'Link copied' : 'Save trip'}</button>
          </div>
        </header>
        <div style={{ position: 'absolute', left: wide ? 40 : 16, right: wide ? 40 : 16, bottom: 32, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 32, color: '#fff', flexWrap: wide ? 'nowrap' : 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 640, maxWidth: '100%' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <input className="hr-title" value={titleIn} placeholder={autoTitle} aria-label="Trip title" spellCheck={false}
                onChange={e => setTitleIn(e.target.value)} onBlur={e => saveTitle(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
                style={{ flex: 1, minWidth: 0, margin: '0 0 0 -8px', padding: '2px 8px', border: 'none', borderRadius: 10, background: 'transparent', color: '#fff',
                  font: `700 ${wide ? 48 : 34}px/1.1 ${FONT}`, letterSpacing: '-.04em', outline: 'none', textOverflow: 'ellipsis' }} />
              {titleEdited && (
                <button className="hr-ghost" onClick={() => saveTitle(null)} style={{ flexShrink: 0, border: '1px solid rgba(255,255,255,.7)', background: 'transparent', color: '#fff', borderRadius: 999, padding: '6px 12px', fontSize: 13, fontWeight: 600 }}>Reset</button>
              )}
            </div>
            <span style={{ fontSize: 18, fontWeight: 600 }}>{names.join(' → ')}</span>
            <span style={{ fontSize: 15 }}>{meta}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: wide ? 'flex-end' : 'flex-start', gap: 6, textAlign: wide ? 'right' : 'left' }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>Estimated total · {cap(tier)}</span>
            <span style={{ fontSize: wide ? 60 : 44, lineHeight: 1, fontWeight: 700, letterSpacing: '-.045em', fontVariantNumeric: 'tabular-nums' }}>{cur} {fmtN(r10(mid(B.total)))}</span>
            <span style={{ fontSize: 15 }}>{cur} {fmtN(r10(mid(B.total) / pax))} per person</span>
          </div>
        </div>
      </div>

      {/* body */}
      <div style={{ padding: wide ? '32px 40px 64px' : '24px 16px 48px', display: 'grid', gridTemplateColumns: wide ? 'minmax(0,1fr) 340px' : 'minmax(0,1fr)', gap: 32, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <h2 style={{ margin: 0, fontSize: 24, fontWeight: 700, letterSpacing: '-.02em' }}>Your stops</h2>
            <span style={{ fontSize: 14, color: MUTED }}>Pick how you travel between stops · {cap(tier)}</span>
          </div>
          {err && <div style={{ fontSize: 14, color: '#a8432a' }}>{err}</div>}
          <div style={{ display: 'flex', flexDirection: 'column', opacity: busy ? 0.6 : 1, transition: 'opacity .2s' }}>{flow}</div>
        </div>

        <div style={{ position: wide ? 'sticky' : 'static', top: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <aside style={{ position: 'relative', background: CARD, borderRadius: 22, padding: 24, display: 'flex', flexDirection: 'column', gap: 18, boxShadow: '0 8px 30px rgba(40,30,20,.08)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: MUTED }}>Estimated total</span>
              <span style={{ fontSize: 34, fontWeight: 700, letterSpacing: '-.03em', lineHeight: 1.05, fontVariantNumeric: 'tabular-nums' }}>{money(B.total)}</span>
              <span style={{ fontSize: 14, color: MUTED }}>{cur} {fmtN(r10(mid(B.total) / pax))} per person</span>
            </div>
            <span style={{ alignSelf: 'flex-start', padding: '6px 12px', borderRadius: 999, fontSize: 13, fontWeight: 600, background: diff > 0 ? '#f6e1d8' : '#e3eadb', color: diff > 0 ? '#8f3b22' : '#3e5a2c' }}>
              {diff > 0 ? `Over budget by ${cur} ${fmtN(r10(diff))}` : `${cur} ${fmtN(r10(-diff))} under budget`}
            </span>
            <div style={{ display: 'flex', gap: 2, background: '#ebe6dc', borderRadius: 999, padding: 3 }}>
              {TIERS.map(([k, label]) => (
                <button key={k} disabled={busy} onClick={() => k !== tier && adjust({ tier: k })} style={{ flex: 1, border: 'none', borderRadius: 999, padding: '8px 0', fontSize: 13, fontWeight: 600,
                  background: k === tier ? INK : 'transparent', color: k === tier ? '#f7f5f0' : INK }}>{label}</button>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {cats.map(([k, m]) => (
                <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}><span>{CAT[k] || k}</span><span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{money(m)}</span></div>
                  <div style={{ height: 6, borderRadius: 999, background: '#ebe6dc', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: (mid(m) / mid(B.total) * 100).toFixed(1) + '%', background: CATCOL[k] || '#9a958a', borderRadius: 999, transition: 'width .5s' }} />
                  </div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 13, color: MUTED }}>Budget {cur} {fmtN(rep.budget)}</div>
            {changed && (
              <button onClick={() => adjust({ reset: true })} disabled={busy} style={{ alignSelf: 'flex-start', border: 'none', background: 'transparent', padding: 0, fontSize: 13, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 3 }}>Undo my changes</button>
            )}
            <button className="hr-dark" onClick={copyLink} style={{ border: 'none', background: INK, color: '#f7f5f0', borderRadius: 14, height: 50, fontSize: 15, fontWeight: 600 }}>{copied ? 'Link copied – the trip is saved' : 'Save trip'}</button>
          </aside>
          {rep.savings.length > 0 && diff > 0 && (
            <div style={{ background: CARD, borderRadius: 20, padding: 24, display: 'flex', flexDirection: 'column', gap: 14, boxShadow: '0 1px 3px rgba(40,30,20,.06)' }}>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, letterSpacing: '-.02em' }}>Ways to save</h2>
              {rep.savings.slice(0, 4).map(v => (
                <button key={v.id} disabled={busy} onClick={() => adjust({ apply_saving: v.id })} title="Apply this change"
                  style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, border: 'none', borderTop: `1px solid ${LINE}`, background: 'transparent', textAlign: 'left', padding: '12px 0 0' }}>
                  <span style={{ fontSize: 14 }}>{v.label}</span>
                  <span style={{ fontSize: 14, fontWeight: 600, color: '#3e5a2c', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>− {money(v.saves)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {openStop && (() => {
        const i = res.places.findIndex(p => p.name === openStop)
        if (i < 0) return null
        const p = res.places[i], L = res.places.length, n = nightsOf(p.name)
        const at = adj.accommodation_tier[p.name] || tier, ft = adj.food_tier[p.name] || tier
        const atts = p.attractions.map(a => ({ a, on: isOn(a.id), sight: a.kind === 'sight', free: !(a.price && a.price.max > 0) }))
        const shown = atts.filter(x => filter === 'all' || (filter === 'sight') === x.sight)
        const actSum = B.lines.filter(l => l.category === 'activities' && l.label.startsWith(p.name + ':')).reduce((x, l) => x + mid(l.total), 0)
        const picker = (field: 'accommodation_per_night' | 'meal_cost', ct: Tier, key: 'accommodation_tier' | 'food_tier', unit: string) => (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 4, background: '#ebe6dc', borderRadius: 12, padding: 3 }}>
            {TIERS.map(([k, label]) => {
              const on = k === ct
              return (
                <button key={k} disabled={busy} aria-pressed={on} onClick={() => !on && adjust({ [key]: { ...adj[key], [p.name]: k } })}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', padding: '6px 10px', border: 'none', borderRadius: 9, background: on ? INK : CARD, color: on ? '#fff' : INK, transition: 'background .2s, color .2s' }}>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{label}</span>
                  <span style={{ fontSize: 11, color: on ? '#cfc9bd' : MUTED, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{avgUsd(p[field][k])}{unit}</span>
                </button>
              )
            })}
          </div>
        )
        const src = (field: string) => p.price_sources?.[field]
          ? <a href={p.price_sources[field]} target="_blank" rel="noreferrer" style={{ fontSize: 11, fontWeight: 500, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 2 }}>source ↗</a>
          : null
        return (
          <>
            <div onClick={() => setOpenStop(null)} style={{ position: 'fixed', inset: 0, zIndex: 30, background: 'rgba(20,18,15,.5)', backdropFilter: 'blur(3px)' }} />
            <div role="dialog" aria-modal="true" aria-label={p.name} style={{ position: 'fixed', zIndex: 31, top: wide ? 40 : 12, left: '50%', transform: 'translateX(-50%)', width: 'min(1000px, calc(100vw - 24px))',
              maxHeight: `calc(100vh - ${wide ? 80 : 24}px)`, background: CARD, borderRadius: 28, overflow: 'hidden', boxShadow: '0 30px 80px rgba(20,18,15,.35)', display: 'flex', flexDirection: 'column' }}>
              <div style={{ position: 'relative', height: 200, flexShrink: 0, background: '#cfc6b6' }}>
                <div role="img" aria-label={p.name} style={{ position: 'absolute', inset: 0, background: bg(p.name) }} />
                <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(rgba(20,18,15,.3),rgba(20,18,15,0) 35%,rgba(20,18,15,.82))' }} />
                <button className="hr-close" onClick={() => setOpenStop(null)} aria-label="Close" style={{ position: 'absolute', top: 16, right: 16, width: 40, height: 40, border: 'none', borderRadius: '50%', background: 'rgba(255,253,248,.94)', color: INK, fontSize: 22, lineHeight: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>×</button>
                <div style={{ position: 'absolute', left: 28, right: 28, bottom: 22, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 20, color: '#fff' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>Stop {i + 1} · {p.country} · {plural(n, 'night')}</span>
                    <span style={{ fontSize: wide ? 46 : 32, fontWeight: 700, letterSpacing: '-.04em', lineHeight: 1 }}>{p.name}</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>Cost at this stop</span>
                    <span style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-.03em', fontVariantNumeric: 'tabular-nums' }}>{cur} {fmtN(r10(mid(stopCost(p.name))))}</span>
                  </div>
                </div>
              </div>
              <div style={{ overflowY: 'auto', padding: '24px 28px 28px', display: 'flex', flexDirection: 'column', gap: 22, scrollbarWidth: 'thin' }}>
                {(p.known_for || '').length > 3 && <p style={{ margin: 0, maxWidth: 760, fontSize: 20, lineHeight: 1.4, fontWeight: 600, letterSpacing: '-.01em' }}>{p.known_for}</p>}
                {(p.summary || '').length > 3 && <p style={{ margin: '-10px 0 0', maxWidth: 760, fontSize: 15, lineHeight: 1.55, color: '#4a4740', fontFamily: BODY }}>{p.summary}</p>}
                <div style={{ display: 'grid', gridTemplateColumns: wide ? 'minmax(0,1.45fr) minmax(0,1fr)' : 'minmax(0,1fr)', gap: 32, alignItems: 'start' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 28, minWidth: 0 }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.01em' }}>Things to do</span>
                        <div style={{ display: 'flex', gap: 4 }}>
                          {([['all', 'All'], ['sight', 'Sights'], ['activity', 'Activities']] as const).map(([k, l]) => (
                            <button key={k} onClick={() => setFilter(k)} style={{ padding: '5px 11px', borderRadius: 999, border: `1px solid ${k === filter ? INK : '#cfc9bd'}`, background: k === filter ? INK : 'transparent', color: k === filter ? '#fff' : INK, fontSize: 12, fontWeight: 600 }}>{l}</button>
                          ))}
                        </div>
                      </div>
                      <span style={{ fontSize: 13, color: MUTED, fontVariantNumeric: 'tabular-nums' }}>
                        {atts.filter(x => x.on).length} of {atts.length} in your plan · {cur} {fmtN(r10(actSum))} for {plural(pax, 'traveller')}
                      </span>
                      <div style={{ display: 'flex', flexDirection: 'column' }}>
                        {shown.map(({ a, on, sight, free }) => (
                          <div key={a.id} style={{ display: 'grid', gridTemplateColumns: '36px minmax(0,1fr) auto', gap: 12, alignItems: 'center', padding: '12px 0', borderTop: `1px solid ${LINE}` }}>
                            <span style={{ width: 36, height: 36, borderRadius: 10, background: sight ? '#e6e3ef' : '#efe2d8', color: sight ? '#4b4d78' : '#8a4a2c', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, letterSpacing: '.03em', textTransform: 'uppercase', opacity: on ? 1 : 0.7 }}>{sight ? 'See' : 'Do'}</span>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, opacity: on ? 1 : 0.7 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                <span style={{ fontSize: 15, fontWeight: 700 }}>{a.name}</span>
                                <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 999, background: on ? '#e3eadb' : '#f0ebe1', color: on ? '#3e5a2c' : MUTED }}>
                                  {on ? (dayOf[a.id] && sched.has(a.id) ? 'Day ' + dayOf[a.id] : 'Added') : 'Not in plan'}
                                </span>
                              </div>
                              <span style={{ fontSize: 13, color: '#4a4740', fontFamily: BODY }}>{a.description} · <strong style={{ fontWeight: 700, color: INK, fontVariantNumeric: 'tabular-nums' }}>{free ? 'Free' : usdR(a.price!) + ' p.p.'}</strong></span>
                            </div>
                            <button className="hr-att" disabled={busy} onClick={() => toggleAtt(a.id)} style={{ minWidth: 76, height: 30, padding: '0 12px', borderRadius: 999, border: `1px solid ${on ? '#cfc9bd' : INK}`, background: on ? CARD : INK, color: on ? INK : '#fff', fontSize: 12, fontWeight: 700 }}>{on ? 'Remove' : 'Add'}</button>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.01em', paddingBottom: 8 }}>Day plan</span>
                      {res.days.filter(d => d.place === p.name).map(d => {
                        const items = d.attraction_ids.filter(aid => !skipped.has(aid)).map(aid => p.attractions.find(a => a.id === aid)?.name).filter(Boolean)
                        return (
                          <div key={d.day} style={{ display: 'grid', gridTemplateColumns: '64px minmax(0,1fr)', gap: 12, padding: '10px 0', borderTop: `1px solid ${LINE}` }}>
                            <span style={{ fontSize: 13, fontWeight: 700, color: '#bc7654', paddingTop: 1 }}>Day {d.day}</span>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              <span style={{ fontSize: 15, fontWeight: 700 }}>{d.title || p.name}</span>
                              <span style={{ fontSize: 13, color: '#4a4740' }}>{items.length ? items.join(' · ') : 'Free time'}</span>
                              {d.notes && <span style={{ fontSize: 12, color: MUTED, fontFamily: BODY }}>{d.notes}</span>}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
                    <div style={{ background: '#f7f4ee', borderRadius: 18, padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
                      <span style={{ fontSize: 16, fontWeight: 700 }}>Your stay</span>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <span style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 600, color: '#4a4740' }}>Hotel {src('accommodation_per_night')}</span>
                        {picker('accommodation_per_night', at, 'accommodation_tier', ' / night')}
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <span style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 600, color: '#4a4740' }}>Food {src('meal_cost')}</span>
                        {picker('meal_cost', ft, 'food_tier', ' / meal')}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, paddingTop: 4 }}>
                        <span style={{ fontWeight: 600, color: '#4a4740' }}>Local transport {src('local_transport_per_day')}</span>
                        <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{avgUsd(p.local_transport_per_day[tier])} / day</span>
                      </div>
                      <span style={{ fontSize: 12, color: MUTED }}>Applies to {p.name} only. The total updates right away.</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      <span style={{ fontSize: 16, fontWeight: 700 }}>What to eat</span>
                      {p.local_food.length ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          {p.local_food.map(f => (
                            <div key={f.name} style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '10px 0', borderTop: `1px solid ${LINE}` }}>
                              <span style={{ fontSize: 15, fontWeight: 700 }}>{cap(f.name)}</span>
                              {(f.description || '').length > 3 && <span style={{ fontSize: 13, lineHeight: 1.45, color: '#4a4740', fontFamily: BODY }}>{f.description}</span>}
                            </div>
                          ))}
                        </div>
                      ) : <span style={{ fontSize: 13, color: MUTED }}>No local dishes listed yet.</span>}
                      <span style={{ fontSize: 13, color: MUTED }}>A meal costs about {avgUsd(p.meal_cost[ft])} per person in {cap(ft)}.</span>
                    </div>
                    {p.sources.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>Sources</span>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          {p.sources.slice(0, 6).map(u => (
                            <a key={u} className="hr-src" href={u} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, padding: '4px 10px', borderRadius: 999, border: '1px solid #e2dccf', color: '#4a4740' }}>{host(u)}</a>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
              {L > 1 && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '12px 20px', borderTop: `1px solid ${LINE}`, background: '#f7f4ee', flexShrink: 0 }}>
                  <button className="hr-pillbtn" onClick={() => setOpenStop(res.places[(i - 1 + L) % L].name)} style={navPill}>← {res.places[(i - 1 + L) % L].name}</button>
                  <button className="hr-pillbtn" onClick={() => setOpenStop(res.places[(i + 1) % L].name)} style={navPill}>{res.places[(i + 1) % L].name} →</button>
                </div>
              )}
            </div>
          </>
        )
      })()}
    </div>
  )
}

const navPill: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, height: 38, padding: '0 16px', border: '1px solid #cfc9bd', borderRadius: 999, background: CARD, fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap' }

function Kv({ k, v }: { k: string; v: string }) {
  return <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13 }}><span style={{ color: MUTED }}>{k}</span><span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{v}</span></div>
}
