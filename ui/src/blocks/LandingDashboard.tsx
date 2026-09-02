import {
    ArrowDownRight,
    ArrowUpRight,
    Activity,
    AlertTriangle,
    BarChart,
    Users,
} from 'lucide-react';
import type { Case } from '../types';

export function LandingDashboard({
    onInvestigate,
    availableCases,
}: {
    onInvestigate: (id: string) => void;
    availableCases: Case[];
}) {
    const anomalyCase = availableCases.find(
        (c) => c.trigger.kpi === 'net_revenue'
    );

    return (
        <div className="dashboard-container">
            <header className="dashboard-header">
                <div>
                    <h1>Command Center</h1>
                    <p className="muted">Live Business Intelligence & Alerts</p>
                </div>
                <div className="status-badge">
                    <span className="dot active"></span> Pipeline Connected
                </div>
            </header>

            <section className="kpi-grid">
                <div className="kpi-card alert-active">
                    <div className="kpi-card-header">
                        <h3>Net Revenue</h3>
                        <Activity className="icon alert" />
                    </div>
                    <div className="kpi-value">₹24.8 Cr</div>
                    <div className="kpi-change alert">
                        <ArrowDownRight size={18} /> 8.0% (₹2.4 Cr)
                    </div>
                    <div className="kpi-alert-footer">
                        <AlertTriangle size={14} /> Anomaly detected
                        {anomalyCase && (
                            <button
                                className="investigate-btn"
                                onClick={() => onInvestigate(anomalyCase.id)}
                            >
                                Investigate
                            </button>
                        )}
                    </div>
                </div>

                <div className="kpi-card">
                    <div className="kpi-card-header">
                        <h3>Gross Renewal Rate</h3>
                        <BarChart className="icon" />
                    </div>
                    <div className="kpi-value">92.4%</div>
                    <div className="kpi-change positive">
                        <ArrowUpRight size={18} /> 1.2%
                    </div>
                    <div className="kpi-footer muted">Metrics stable</div>
                </div>

                <div className="kpi-card">
                    <div className="kpi-card-header">
                        <h3>Expansion ARR</h3>
                        <Activity className="icon" />
                    </div>
                    <div className="kpi-value">₹4.2 Cr</div>
                    <div className="kpi-change positive">
                        <ArrowUpRight size={18} /> 4.5%
                    </div>
                    <div className="kpi-footer muted">Metrics stable</div>
                </div>

                <div className="kpi-card">
                    <div className="kpi-card-header">
                        <h3>Support Resolution</h3>
                        <Users className="icon" />
                    </div>
                    <div className="kpi-value">1.4 Hours</div>
                    <div className="kpi-change positive">
                        <ArrowDownRight size={18} /> 12.0%
                    </div>
                    <div className="kpi-footer muted">Metrics stable</div>
                </div>
            </section>

            <section className="recent-cases">
                <h2>Recent Automated Investigations</h2>
                <table className="cases-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>KPI</th>
                            <th>Region</th>
                            <th>Status</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {availableCases.map((c) => (
                            <tr key={c.id}>
                                <td className="mono">{c.id.slice(0, 15)}...</td>
                                <td>
                                    {c.trigger.kpi.replace('_', ' ').replace(/\b\w/g, (l) =>
                                        l.toUpperCase()
                                    )}
                                </td>
                                <td>{c.trigger.dimensions?.region || 'All'}</td>
                                <td>
                                    {c.id.includes('scenario_b') ? (
                                        <span className="badge warning">Low Confidence</span>
                                    ) : c.id.includes('scenario_d') ? (
                                        <span className="badge neutral">False Alarm</span>
                                    ) : (
                                        <span className="badge critical">Confirmed</span>
                                    )}
                                </td>
                                <td>
                                    <button
                                        className="view-btn"
                                        onClick={() => onInvestigate(c.id)}
                                    >
                                        View Case
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </section>
        </div>
    );
}
