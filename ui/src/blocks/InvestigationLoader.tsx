import { useState, useEffect } from 'react';
import { Loader2, CheckCircle2 } from 'lucide-react';

const STEPS = [
    { id: 's1', label: 'Verify: Checking data freshness & anomalies...', duration: 600 },
    { id: 's2', label: 'Decompose: Breaking down metrics by account & channel...', duration: 800 },
    { id: 's3', label: 'Investigate: Querying LLM for unstructured CRM notes...', duration: 1200 },
    { id: 's4', label: 'Challenge: Running Locality, Timing, and DiD falsification tests...', duration: 900 },
    { id: 's5', label: 'Adjudicate: Formulating confidence and recommendations...', duration: 500 },
];

export function InvestigationLoader({ onComplete }: { onComplete: () => void }) {
    const [activeStep, setActiveStep] = useState(0);

    useEffect(() => {
        let currentStep = 0;
        const runSteps = () => {
            if (currentStep >= STEPS.length) {
                setTimeout(onComplete, 500);
                return;
            }
            setActiveStep(currentStep);
            setTimeout(() => {
                currentStep++;
                runSteps();
            }, STEPS[currentStep].duration);
        };

        runSteps();
    }, [onComplete]);

    return (
        <div className="loader-overlay">
            <div className="loader-modal">
                <h2>CaseFile AI Reasoning Engine</h2>
                <p className="muted">Running deterministic verification & hypothesis testing...</p>

                <div className="loader-steps">
                    {STEPS.map((step, index) => {
                        const isCompleted = index < activeStep;
                        const isActive = index === activeStep;
                        const isPending = index > activeStep;

                        return (
                            <div
                                key={step.id}
                                className={`loader-step ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''
                                    } ${isPending ? 'pending' : ''}`}
                            >
                                <div className="step-icon">
                                    {isCompleted ? (
                                        <CheckCircle2 className="icon-success" size={20} />
                                    ) : isActive ? (
                                        <Loader2 className="spinner" size={20} />
                                    ) : (
                                        <div className="circle-placeholder" />
                                    )}
                                </div>
                                <span>{step.label}</span>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
