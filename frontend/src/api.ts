// Types mirror backend/app/models.py
export type Tier = 'budget' | 'comfort' | 'luxury'
export const TIERS: Tier[] = ['budget', 'comfort', 'luxury']
export const TIER_LABEL: Record<Tier, string> = { budget: 'Budget', comfort: 'Comfort', luxury: 'Luxury' }

export const TRIP_TYPES = {
  backpacking: 'Backpacking',
  city: 'City trip',
  beach: 'Beach & relax',
  adventure: 'Adventure & nature',
  culture: 'Culture',
  roadtrip: 'Road trip',
  family: 'Family',
} as const
export type TripType = keyof typeof TRIP_TYPES

export interface GeoPlace { name: string; country: string; state: string; type: string; lat: number; lon: number }

export interface StopInput { name: string; nights: number | null; lat?: number; lon?: number }

export interface TripRequest {
  origin: string
  stops: { name: string; nights: number | null }[]
  return_to_origin: boolean
  start_date: string | null
  end_date: string | null
  days: number | null
  travelers: number
  budget: number
  currency: string
  trip_type: TripType
  interests: string[]
  tier: Tier
}

export interface PriceRange { min: number; max: number }
export interface TierPrices { budget: PriceRange; comfort: PriceRange; luxury: PriceRange }
export interface Attraction { id: string; name: string; kind: 'sight' | 'activity'; description: string; price: PriceRange | null }
export interface Place {
  name: string; country: string; known_for: string; summary: string
  attractions: Attraction[]; local_food: { name: string; description: string }[]
  accommodation_per_night: TierPrices; meal_cost: TierPrices; local_transport_per_day: TierPrices
  sources: string[]
  price_sources?: Record<string, string>  // price field -> page the price comes from
}
export interface TransportOption { mode: string; description: string; duration_hours: number; price: PriceRange; source_url?: string | null }
export interface Leg { from_place: string; to_place: string; options: TransportOption[]; sources: string[] }
export interface Day { day: number; place: string; title: string; attraction_ids: string[]; notes: string }
export interface TripResearch { places: Place[]; legs: Leg[]; nights: { place: string; nights: number }[]; days: Day[] }

export interface CostLine { category: string; label: string; unit: string; total: PriceRange; place?: string | null }
export interface LegChoice { leg_index: number; from_place: string; to_place: string; fastest: number; cheapest: number; best_value: number; chosen: number }
export interface Breakdown {
  tier: Tier; currency: string; lines: CostLine[]; by_category: Record<string, PriceRange>; total: PriceRange
  leg_choices: LegChoice[]; by_place?: Record<string, PriceRange>
}
export interface SavingOption { id: string; label: string; saves: PriceRange }
export interface Adjustments {
  leg_choice: Record<string, number>; accommodation_tier: Record<string, Tier>; food_tier: Record<string, Tier>
  skipped_attractions: string[]; added_attractions?: string[]; nights: Record<string, number>
}
/** Body of POST /adjust: every field optional; adjustment fields replace that field, the rest stays. */
export type AdjustBody = { tier?: Tier; apply_saving?: string; reset?: boolean } & Partial<Adjustments>
export interface BudgetReport {
  tiers: Record<Tier, Breakdown>; selected_tier: Tier; selected: Breakdown
  budget: number; currency: string; over_budget: boolean; over_by: number
  savings: SavingOption[]; adjustments: Adjustments
}
export interface TripEvent { seq: number; ts: number; message: string }
export interface Trip {
  id: string; status: 'queued' | 'researching' | 'synthesizing' | 'done' | 'error'
  request: TripRequest; events: TripEvent[]; research: TripResearch | null
  usd_rate: number | null; report: BudgetReport | null; error: string | null; title?: string | null
  usage: { llm_calls: number; input_tokens: number; output_tokens: number; llm_cost_usd: number; search_credits: number;
    llm_by_provider?: Record<string, number> }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  currencies: () => call<Record<string, string>>('/api/currencies'),
  search: (q: string, signal?: AbortSignal) => call<GeoPlace[]>(`/api/geo/search?q=${encodeURIComponent(q)}`, { signal }),
  reverse: (lat: number, lon: number) => call<GeoPlace>(`/api/geo/reverse?lat=${lat}&lon=${lon}`),
  createTrip: (req: TripRequest) => call<{ id: string }>('/api/trips', { method: 'POST', body: JSON.stringify(req) }),
  trip: (id: string) => call<Trip>(`/api/trips/${id}`),
  adjust: (id: string, body: AdjustBody) =>
    call<BudgetReport>(`/api/trips/${id}/adjust`, { method: 'POST', body: JSON.stringify(body) }),
  patchTrip: (id: string, body: { title: string | null }) =>
    call<Trip>(`/api/trips/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
}

export function fmtMoney(r: PriceRange, currency: string): string {
  const f = (n: number) => Math.round(n).toLocaleString('en-US')
  return Math.round(r.min) === Math.round(r.max) ? `${f(r.min)} ${currency}` : `${f(r.min)}–${f(r.max)} ${currency}`
}
