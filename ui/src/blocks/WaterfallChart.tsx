import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    Cell
} from 'recharts';
import { crore } from '../format';

type WaterfallData = {
    name: string;
    value: number;
    start: number;
    end: number;
    fill: string;
};

export function WaterfallChart({ data }: { data: { key: string; delta: number }[] }) {
    let currentTotal = 0;

    // Create waterfall segments
    const chartData: WaterfallData[] = data.map((item) => {
        const start = currentTotal;
        const end = start + item.delta;
        currentTotal = end;

        return {
            name: item.key.length > 15 ? item.key.slice(0, 15) + '...' : item.key,
            value: item.delta,
            start: Math.min(start, end),
            end: Math.max(start, end),
            fill: item.delta < 0 ? '#ef4444' : '#10b981' // red for negative, green for positive
        };
    });

    // Calculate domain padding
    const minVal = Math.min(...chartData.map(d => d.start), 0);
    const maxVal = Math.max(...chartData.map(d => d.end), 0);

    // Custom tooltips
    const CustomTooltip = ({ active, payload }: any) => {
        if (active && payload && payload.length) {
            const data = payload[0].payload;
            return (
                <div className="chart-tooltip">
                    <p className="chart-tooltip-label">{data.name}</p>
                    <p className="chart-tooltip-value" style={{ color: data.fill }}>
                        {data.value > 0 ? '+' : ''}{crore(data.value)}
                    </p>
                </div>
            );
        }
        return null;
    };

    return (
        <div style={{ width: '100%', height: 300, marginTop: '2rem' }}>
            <ResponsiveContainer>
                <BarChart
                    data={chartData}
                    margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
                >
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#333" />
                    <XAxis dataKey="name" tick={{ fill: '#888', fontSize: 12 }} axisLine={false} tickLine={false} />
                    <YAxis
                        domain={[minVal * 1.1, maxVal > 0 ? maxVal * 1.1 : 0]}
                        tickFormatter={(val) => `₹${(val / 10000000).toFixed(1)}Cr`}
                        tick={{ fill: '#888', fontSize: 12 }}
                        axisLine={false}
                        tickLine={false}
                    />
                    <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.05)' }} />
                    <Bar dataKey="end" fill="transparent" />
                    <Bar dataKey="value">
                        {chartData.map((entry, index) => (
                            <Cell key={`cell-${index}`} fill={entry.fill} />
                        ))}
                    </Bar>
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}
