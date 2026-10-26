// Map data and geometry shared by the trip builder and the results page (ported from the design handoff).
import { useEffect, useState } from 'react'

export interface Country { n: string; c: string; d: string; x: number; y: number; s: number }
export interface Destination {
  id: string; c: string; mood: 'City' | 'Nature' | 'Beach'; name: string; country: string; season: string
  img?: string; src?: string; file?: string; lat?: number; lon?: number; cn?: string; wiki?: string; landmark?: string
}
/** A place on the map: lat/lon plus its position in the SVG map (viewBox 0 0 1376 768). */
export interface Pt { name: string; country: string; c?: string; lat: number; lon: number; x: number; y: number }

export const MAP_W = 1376
export const MAP_H = 768

export const MAPCOL: Record<string, string> = {
  namerica: '#a1a899', samerica: '#c8af83', europe: '#8a8cb2', africa: '#c49a8e', asia: '#979e75', oceania: '#bc7654',
}
export const CONTINENTS = [
  { id: 'europe', name: 'Europe', color: '#7f82ab' },
  { id: 'asia', name: 'Asia', color: '#8a9567' },
  { id: 'africa', name: 'Africa', color: '#b8975f' },
  { id: 'namerica', name: 'North America', color: '#8b9787' },
  { id: 'samerica', name: 'South America', color: '#a98a55' },
  { id: 'oceania', name: 'Oceania', color: '#c2714e' },
]
export const CONT_NAME: Record<string, string> = Object.fromEntries(CONTINENTS.map(c => [c.id, c.name]))
/** Continent label positions on the homepage map, in % of the map box. */
export const CONT_POS: Record<string, [number, number]> = {
  namerica: [27, 30], samerica: [35, 62], europe: [52, 22], africa: [54, 52], asia: [70, 28], oceania: [83, 64],
}

/** Continent colour mixed toward the page background (#f7f5f0) for countries not on the route. */
export function lighten(hex: string, amount: number): string {
  const a = [1, 3, 5].map(i => parseInt(hex.substr(i, 2), 16)), b = [247, 245, 240]
  return 'rgb(' + a.map((v, j) => Math.round(v + (b[j] - v) * amount)).join(',') + ')'
}

const PLEN = [1, .9986, .9954, .99, .9761, .9394, .8936, .8435, .7881, .7313]
const plen = (lat: number) => {
  const a = Math.min(89.9, Math.abs(lat)) / 10, i = Math.floor(a)
  return PLEN[i] + (PLEN[Math.min(9, i + 1)] - PLEN[i]) * (a - i)
}
export const proj = (lat: number, lon: number): [number, number] => [712.8 + 3.037 * lon * plen(lat), 385.3 - 3.534 * lat]
export const unproj = (x: number, y: number): [number, number] => {
  const lat = (385.3 - y) / 3.534
  return [lat, (x - 712.8) / (3.037 * plen(lat))]
}
export const km = (a: { lat: number; lon: number }, b: { lat: number; lon: number }) => {
  const r = Math.PI / 180, dl = (b.lat - a.lat) * r, dn = (b.lon - a.lon) * r
  const h = Math.sin(dl / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dn / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(h))
}
export const fmtKm = (d: number) => Math.round(d).toLocaleString('en-US') + ' km'

/** Curved route segment between two map points (bulges upward). */
export function arc(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1
  let nx = -dy / L, ny = dx / L
  if (ny > 0) { nx = -nx; ny = -ny }
  return `M${a.x},${a.y} Q${mx + nx * L * 0.2},${my + ny * L * 0.2} ${b.x},${b.y}`
}

export function toPt(p: { name: string; country: string; c?: string; lat: number; lon: number }): Pt {
  const [x, y] = proj(p.lat, p.lon)
  return { name: p.name, country: p.country, c: p.c, lat: p.lat, lon: p.lon, x, y }
}

/** Popular starting points (the design's offline list). */
export const CITIES: Pt[] = (`Basel|Switzerland|europe|47.56|7.59;Zürich|Switzerland|europe|47.37|8.54;Geneva|Switzerland|europe|46.2|6.14;Bern|Switzerland|europe|46.95|7.45;London|United Kingdom|europe|51.5|-0.13;Paris|France|europe|48.86|2.35;Berlin|Germany|europe|52.52|13.4;Munich|Germany|europe|48.14|11.58;Vienna|Austria|europe|48.21|16.37;Milan|Italy|europe|45.46|9.19;Madrid|Spain|europe|40.42|-3.7;Athens|Greece|europe|37.98|23.73;Reykjavík|Iceland|europe|64.15|-21.94;São Paulo|Brazil|samerica|-23.55|-46.63;Santiago|Chile|samerica|-33.45|-70.67;Lima|Peru|samerica|-12.05|-77.04;La Paz|Bolivia|samerica|-16.5|-68.15;Bogotá|Colombia|samerica|4.71|-74.07;Mendoza|Argentina|samerica|-32.89|-68.84;Punta Arenas|Chile|samerica|-53.16|-70.91;Los Angeles|United States of America|namerica|34.05|-118.24;Chicago|United States of America|namerica|41.88|-87.63;Toronto|Canada|namerica|43.65|-79.38;Cancún|Mexico|namerica|21.16|-86.85;Kyoto|Japan|asia|35.01|135.77;Seoul|South Korea|asia|37.57|126.98;Hanoi|Vietnam|asia|21.03|105.85;Delhi|India|asia|28.61|77.21;Kathmandu|Nepal|asia|27.72|85.32;Nairobi|Kenya|africa|-1.29|36.82;Windhoek|Namibia|africa|-22.56|17.08;Queenstown|New Zealand|oceania|-45.03|168.66`)
  .split(';').map(l => { const [name, country, c, lat, lon] = l.split('|'); return toPt({ name, country, c, lat: +lat, lon: +lon }) })

export const norm = (s: string | undefined) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

// --- data files (public/assets), loaded once per page load ------------------------------------------------

const cache = new Map<string, Promise<unknown>>()
function load<T>(url: string): Promise<T> {
  if (!cache.has(url)) {
    const get = (n: number): Promise<T> => fetch(url).then(r => r.ok ? r.json() : Promise.reject(r.status))
      .catch(e => n > 0 ? new Promise(res => setTimeout(res, 400)).then(() => get(n - 1)) : Promise.reject(e))
    cache.set(url, get(4))
  }
  return cache.get(url) as Promise<T>
}

function useAsset<T>(url: string): T | null {
  const [data, setData] = useState<T | null>(null)
  useEffect(() => {
    let live = true
    load<T>(url).then(d => { if (live) setData(d) }).catch(e => console.warn(url, e))
    return () => { live = false }
  }, [url])
  return data
}

export const useCountries = () => useAsset<Country[]>('/assets/countries.json')
export const useDestinations = () => useAsset<Destination[]>('/assets/destinations.json')
export const loadCountries = () => load<Country[]>('/assets/countries.json')
export const loadDestinations = () => load<Destination[]>('/assets/destinations.json')
