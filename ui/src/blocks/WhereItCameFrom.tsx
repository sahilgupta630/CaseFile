import type { ContributionTree } from '../types'
import { crore, share } from '../format'
import { WaterfallChart } from './WaterfallChart'

/**
 * Block 2 — §10's decomposition: by KPI, by account, concentration.
 */
export function WhereItCameFrom({ tree }: { tree: ContributionTree | null }) {
  if (!tree) {
    return (
      <section aria-labelledby="where-h" data-block="where-it-came-from">
        <h2 id="where-h">Where it came from</h2>
        <p className="muted">Not decomposed — the case closed before Stage 2.</p>
      </section>
    )
  }

  const k2 = concentration(tree.by_dimension.account, 2)
  const effective = effectiveSegments(tree.hhi)
  const accountCount = tree.by_dimension.account?.length ?? 0

  return (
    <section aria-labelledby="where-h" data-block="where-it-came-from">
      <h2 id="where-h">Where it came from</h2>

      {/* Waterfall Visualization for Account level contribution */}
      {tree.by_dimension.account && tree.by_dimension.account.length > 0 && (
        <div className="decomposition-chart-wrapper">
          <WaterfallChart data={tree.by_dimension.account} />
        </div>
      )}

      {Object.entries(tree.by_dimension).map(([dimension, nodes]) => (
        <dl key={dimension} className="contribution" data-dimension={dimension}>
          {nodes.map((node) => (
            <div key={node.key} className="contribution-row">
              <dt>{node.key}</dt>
              <dd>
                {crore(node.delta)} ({share(node.share)})
              </dd>
            </div>
          ))}
        </dl>
      ))}
      {k2 !== null && <p className="concentration">concentration K(2) = {k2.toFixed(2)}</p>}
      {effective !== null && (
        <p className="concentration">
          {accountCount} account{accountCount === 1 ? '' : 's'} carr
          {accountCount === 1 ? 'ies' : 'y'} as much risk as {effective.toFixed(1)} equally
          sized {effective.toFixed(1) === '1.0' ? 'one' : 'ones'}
        </p>
      )}
    </section>
  )
}

/** 1 ÷ HHI — the number of equally sized accounts that would concentrate risk
 * the same way §14.1's own `hhi` already measures. Read alongside K(2): K(2)
 * says how much of the movement the top two accounts hold, this says how few
 * accounts the whole movement effectively rests on. */
function effectiveSegments(hhi: number | null): number | null {
  if (hhi === null || hhi <= 0) return null
  return 1 / hhi
}

function concentration(nodes: ContributionTree['by_dimension'][string] | undefined, k: number): number | null {
  if (!nodes || nodes.length === 0) return null
  const magnitudes = nodes.map((n) => Math.abs(n.delta)).sort((a, b) => b - a)
  const total = magnitudes.reduce((sum, v) => sum + v, 0)
  if (total === 0) return 0
  return magnitudes.slice(0, k).reduce((sum, v) => sum + v, 0) / total
}
