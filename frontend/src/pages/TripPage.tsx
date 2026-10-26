// Results route: loading overlay while the backend researches (design 8c), then the result page.
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Trip, type TripEvent } from '../api'
import Result from './Result'

const FONT = "'Bricolage Grotesque', sans-serif"

export default function TripPage() {
  const { id } = useParams<{ id: string }>()
  const [trip, setTrip] = useState<Trip | null>(null)
  const [events, setEvents] = useState<TripEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [finishing, setFinishing] = useState(false)
  const [, tick] = useState(0)
  const t0 = useRef(Date.now())

  useEffect(() => {
    if (!id) return
    let source: EventSource | null = null
    t0.current = Date.now()
    api.trip(id).then(t => {
      setTrip(t)
      setEvents(t.events)
      if (t.status === 'done' || t.status === 'error') return
      source = new EventSource(`/api/trips/${id}/events`)
      source.addEventListener('progress', e => {
        const ev = JSON.parse((e as MessageEvent).data) as TripEvent
        setEvents(prev => (prev.some(p => p.seq === ev.seq) ? prev : [...prev, ev]))
      })
      source.addEventListener('status', () => {
        source?.close()
        api.trip(id).then(done => {
          setTrip(done)
          if (done.status === 'done') { setFinishing(true); setTimeout(() => setFinishing(false), 900) }
        }).catch(e => setError(e.message))
      })
    }).catch(e => setError(String(e.message).includes('fetch') ? 'Backend not reachable.' : e.message))
    return () => source?.close()
  }, [id])

  const running = !!trip && trip.status !== 'done' && trip.status !== 'error'
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => tick(x => x + 1), 500)
    return () => clearInterval(t)
  }, [running])

  const ready = trip?.status === 'done' && trip.report && trip.research
  const req = trip?.request
  const names = req ? [req.origin, ...req.stops.map(s => s.name), ...(req.return_to_origin ? [req.origin] : [])] : []
  let pct = 0, msg = 'Loading trip…', title = 'Planning your trip', failed = false
  if (error) { failed = true; title = 'Could not load the trip'; msg = error }
  else if (trip?.status === 'error') { failed = true; title = 'Planning failed'; msg = trip.error || 'Unknown error'; pct = 100 }
  else if (running && req) {
    const expected = 4 + 3 * req.stops.length + 2 * (names.length - 1)
    const el = (Date.now() - t0.current) / 1000
    pct = Math.min(95, Math.max(events.length / expected * 100, 90 * (1 - Math.exp(-el / 45))))
    msg = events.length ? events[events.length - 1].message : 'Queued'
  } else if (ready) { pct = 100; msg = 'Done' }
  const show = !ready || finishing

  return (
    <>
      {ready && <Result trip={trip!} onTrip={setTrip} />}
      <div style={{ position: 'fixed', inset: 0, zIndex: 50, background: '#f7f5f0', fontFamily: FONT, color: '#24231f', opacity: show ? 1 : 0,
        pointerEvents: show ? 'auto' : 'none', visibility: show ? 'visible' : 'hidden',
        transition: show ? 'opacity .6s ease' : 'opacity .6s ease, visibility 0s linear .6s',
        display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: '28vh', gap: 24 }}>
        <Link to="/" style={{ position: 'absolute', top: 22, left: 40, font: `800 28px ${FONT}`, letterSpacing: '-.03em' }}>Holydays</Link>
        <div style={{ fontSize: 15, color: '#6b675e', textAlign: 'center', padding: '0 24px' }}>{names.join(' → ')}</div>
        <div style={{ font: `700 44px ${FONT}`, letterSpacing: '-.03em', textAlign: 'center' }}>{title}</div>
        <div style={{ width: 'min(560px, calc(100vw - 48px))', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 16 }}>
            <span style={{ fontSize: 15, color: '#4a4740', whiteSpace: failed ? 'normal' : 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{msg}</span>
            <span style={{ font: `700 22px ${FONT}`, fontVariantNumeric: 'tabular-nums' }}>{Math.round(pct)}%</span>
          </div>
          <div style={{ height: 8, borderRadius: 999, background: '#e4dfd4', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: pct.toFixed(1) + '%', background: failed ? '#a8432a' : '#24231f', borderRadius: 999, transition: 'width .4s ease' }} />
          </div>
          <div style={{ fontSize: 13, color: '#9a958a' }}>{failed ? 'Check the backend and try again.' : 'This usually takes about a minute.'}</div>
          {failed && id && (
            <div style={{ display: 'flex', gap: 16, fontSize: 14, fontWeight: 600 }}>
              <Link to={`/plan?trip=${id}`} style={{ textDecoration: 'underline', textUnderlineOffset: 3 }}>Edit route and try again</Link>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
