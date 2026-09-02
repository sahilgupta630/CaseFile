import { useEffect, useState } from 'react'
import { CaseFile } from './CaseFile'
import { LandingDashboard } from './blocks/LandingDashboard'
import { InvestigationLoader } from './blocks/InvestigationLoader'
import { PersonaSwitcher } from './PersonaSwitcher'
import { Ledger } from './blocks/Ledger'
import { TelemetryPanel } from './blocks/TelemetryPanel'
import type { Case, EntitledView } from './types'
import caseRealA from '@fixtures/case_real_scenario_a.json'
import caseRealB from '@fixtures/case_real_scenario_b.json'
import caseRealD from '@fixtures/case_real_scenario_d.json'
import entitledRealA from '@fixtures/case_real_scenario_a_entitled.json'

type Screen = 'dashboard' | 'loading' | 'detail' | 'persona'

/**
 * Enhanced for Hackathon Presentation.
 * The flow goes: Dashboard -> Loading Simulation -> Case File Detail.
 */
export function App() {
  const [cases, setCases] = useState<Case[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [screen, setScreen] = useState<Screen>('dashboard')

  useEffect(() => {
    setCases([caseRealA, caseRealB, caseRealD] as unknown as Case[])
  }, [])

  if (!cases) return null

  if (screen === 'dashboard') {
    return (
      <LandingDashboard
        availableCases={cases}
        onInvestigate={(id) => {
          setSelectedId(id)
          setScreen('loading')
        }}
      />
    )
  }

  if (screen === 'loading') {
    return <InvestigationLoader onComplete={() => setScreen('detail')} />
  }

  const selected = cases.find((c) => c.id === selectedId) ?? null
  if (!selected) return null

  if (screen === 'persona') {
    return (
      <>
        <button type="button" className="back-link" onClick={() => setScreen('detail')}>
          ← Back to case
        </button>
        <PersonaSwitcher views={entitledRealA as unknown as Record<string, EntitledView>} />
      </>
    )
  }

  return (
    <>
      <button type="button" className="back-link" onClick={() => setScreen('dashboard')}>
        ← Back to dashboard
      </button>
      <CaseFile case={selected} />
      <Ledger ledger={selected.ledger} />
      <TelemetryPanel telemetry={selected.telemetry} />
      {selected.id === caseRealA.id && (
        <button type="button" className="persona-link" onClick={() => setScreen('persona')}>
          View as another persona →
        </button>
      )}
    </>
  )
}
