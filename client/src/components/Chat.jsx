import { useState, useRef, useEffect, useCallback } from 'react'
import Message from './Message'
import styles from './Chat.module.css'

const API_URL = import.meta.env.VITE_API_URL

// â”€â”€ Streaming inference â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function streamInference(text, onToken, signal) {
    const response = await fetch(`${API_URL}/build-context`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal,
    })

    if (!response.ok) {
        const msg = await response.text().catch(() => response.statusText)
        throw new Error(`Server error ${response.status}: ${msg}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed) continue

            let parsed
            try {
                parsed = JSON.parse(trimmed)
            } catch {
                continue
            }

            onToken(parsed.chunk ?? '')

            if (parsed.is_final) {
                reader.cancel()
                return
            }
        }
    }

    if (buffer.trim()) {
        try {
            const parsed = JSON.parse(buffer.trim())
            onToken(parsed.chunk ?? '')
        } catch {
            // ignore
        }
    }
}

// â”€â”€ Network node SVG logo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function NodeLogo() {
    return (
        <svg width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden>
            {/* central node */}
            <circle cx="15" cy="15" r="4.5" fill="#00d4ff" opacity="0.9" />
            <circle cx="15" cy="15" r="7" stroke="#00d4ff" strokeWidth="0.8" opacity="0.3" />
            {/* outer nodes */}
            <circle cx="4" cy="9" r="2.5" fill="#00ff88" opacity="0.85" />
            <circle cx="26" cy="9" r="2.5" fill="#00ff88" opacity="0.85" />
            <circle cx="4" cy="21" r="2.5" fill="#00ff88" opacity="0.85" />
            <circle cx="26" cy="21" r="2.5" fill="#00ff88" opacity="0.85" />
            {/* edges */}
            <line x1="11.2" y1="12.5" x2="6.2" y2="10.2" stroke="#00d4ff" strokeWidth="0.9" opacity="0.55" />
            <line x1="18.8" y1="12.5" x2="23.8" y2="10.2" stroke="#00d4ff" strokeWidth="0.9" opacity="0.55" />
            <line x1="11.2" y1="17.5" x2="6.2" y2="19.8" stroke="#00d4ff" strokeWidth="0.9" opacity="0.55" />
            <line x1="18.8" y1="17.5" x2="23.8" y2="19.8" stroke="#00d4ff" strokeWidth="0.9" opacity="0.55" />
        </svg>
    )
}

// â”€â”€ Main component â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export default function Chat() {
    const [messages, setMessages] = useState([])
    const [input, setInput] = useState('')
    const [isStreaming, setIsStreaming] = useState(false)
    const [error, setError] = useState(null)
    const [totalTokens, setTotalTokens] = useState(0)
    const [requestCount, setRequestCount] = useState(0)

    const abortRef = useRef(null)
    const bottomRef = useRef(null)
    const textareaRef = useRef(null)
    const idCounter = useRef(0)

    // Auto-scroll
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    // Auto-resize textarea
    useEffect(() => {
        const ta = textareaRef.current
        if (!ta) return
        ta.style.height = 'auto'
        ta.style.height = `${Math.min(ta.scrollHeight, 180)}px`
    }, [input])

    const nextId = () => ++idCounter.current

    const handleSubmit = useCallback(async () => {
        const text = input.trim()
        if (!text || isStreaming) return

        setError(null)
        setInput('')
        setIsStreaming(true)
        setRequestCount(c => c + 1)

        const timestamp = new Date()
        const userMsg = { role: 'user', text, id: nextId(), timestamp }
        const assistantId = nextId()
        const assistantMsg = { role: 'assistant', text: '', id: assistantId, timestamp: new Date(), tokens: 0 }

        setMessages(prev => [...prev, userMsg, assistantMsg])

        const controller = new AbortController()
        abortRef.current = controller

        try {
            await streamInference(
                text,
                (token) => {
                    setTotalTokens(t => t + 1)
                    setMessages(prev =>
                        prev.map(m =>
                            m.id === assistantId
                                ? { ...m, text: m.text ? m.text + ' ' + token : token, tokens: (m.tokens || 0) + 1 }
                                : m
                        )
                    )
                },
                controller.signal,
            )
        } catch (err) {
            if (err.name === 'AbortError') {
                // user cancelled â€” leave partial response as-is
            } else {
                setError(err.message)
                setMessages(prev =>
                    prev.filter(m => !(m.id === assistantId && m.text === ''))
                )
            }
        } finally {
            setIsStreaming(false)
            abortRef.current = null
        }
    }, [input, isStreaming])

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSubmit()
        }
    }

    const handleStop = () => abortRef.current?.abort()
    const handleClear = () => {
        if (isStreaming) abortRef.current?.abort()
        setMessages([])
        setError(null)
    }

    const nodeStatus = isStreaming ? 'streaming' : 'idle'

    return (
        <div className={styles.container}>
            {/* Animated network grid */}
            <div className={styles.gridBg} aria-hidden="true" />

            {/* â”€â”€ Header â”€â”€ */}
            <header className={styles.header}>
                <div className={styles.headerLeft}>
                    <NodeLogo />
                    <div className={styles.headerBrand}>
                        <span className={styles.headerTitle}>DistributedAI</span>
                        <span className={styles.headerSub}>Inference Cluster Â· Stream Mode</span>
                    </div>
                </div>
                <div className={styles.headerRight}>
                    <div className={styles.statusPill} data-status={nodeStatus}>
                        <span className={styles.statusDot} />
                        {nodeStatus === 'streaming' ? 'STREAMING' : 'CONNECTED'}
                    </div>
                    <button
                        className={styles.clearBtn}
                        onClick={handleClear}
                        title="Clear conversation"
                    >
                        Clear
                    </button>
                </div>
            </header>

            {/* â”€â”€ Metrics bar â”€â”€ */}
            <div className={styles.metricsBar}>
                <div className={styles.metric}>
                    <span className={styles.metricLabel}>Endpoint</span>
                    <span className={styles.metricValue}>{API_URL}/infer</span>
                </div>
                <div className={styles.metricDivider} />
                <div className={styles.metric}>
                    <span className={styles.metricLabel}>Protocol</span>
                    <span className={styles.metricValue}>HTTP STREAM</span>
                </div>
                <div className={styles.metricDivider} />
                <div className={styles.metric}>
                    <span className={styles.metricLabel}>Requests</span>
                    <span className={styles.metricValue}>{requestCount}</span>
                </div>
                <div className={styles.metricDivider} />
                <div className={styles.metric}>
                    <span className={styles.metricLabel}>Tokens Rx</span>
                    <span className={styles.metricValue}>{totalTokens}</span>
                </div>
                <div className={styles.metricDivider} />
                <div className={styles.metric}>
                    <span className={styles.metricLabel}>Node Status</span>
                    <span className={styles.metricValue}>
                        {isStreaming ? 'PROCESSING' : 'IDLE'}
                    </span>
                </div>
            </div>

            {/* â”€â”€ Messages â”€â”€ */}
            <main className={styles.messageList}>
                {messages.length === 0 && (
                    <div className={styles.emptyState}>
                        <div className={styles.emptyIcon}>
                            <svg width="72" height="72" viewBox="0 0 72 72" fill="none" aria-hidden>
                                <circle cx="36" cy="36" r="9" stroke="#00d4ff" strokeWidth="1.5" opacity="0.85" />
                                <circle cx="36" cy="36" r="20" stroke="#00d4ff" strokeWidth="0.8" opacity="0.3" strokeDasharray="4 4" />
                                <circle cx="36" cy="36" r="31" stroke="#00d4ff" strokeWidth="0.5" opacity="0.13" strokeDasharray="2 6" />
                                <circle cx="11" cy="20" r="4" stroke="#00ff88" strokeWidth="1.5" opacity="0.75" />
                                <circle cx="61" cy="20" r="4" stroke="#00ff88" strokeWidth="1.5" opacity="0.75" />
                                <circle cx="11" cy="52" r="4" stroke="#00ff88" strokeWidth="1.5" opacity="0.75" />
                                <circle cx="61" cy="52" r="4" stroke="#00ff88" strokeWidth="1.5" opacity="0.75" />
                                <line x1="14.8" y1="22.9" x2="27.4" y2="30.2" stroke="#00d4ff" strokeWidth="0.9" opacity="0.5" />
                                <line x1="57.2" y1="22.9" x2="44.6" y2="30.2" stroke="#00d4ff" strokeWidth="0.9" opacity="0.5" />
                                <line x1="14.8" y1="49.1" x2="27.4" y2="41.8" stroke="#00d4ff" strokeWidth="0.9" opacity="0.5" />
                                <line x1="57.2" y1="49.1" x2="44.6" y2="41.8" stroke="#00d4ff" strokeWidth="0.9" opacity="0.5" />
                            </svg>
                        </div>
                        <p className={styles.emptyTitle}>All Nodes Online</p>
                        <p className={styles.emptyDesc}>
                            Cluster is ready. Send a query to begin distributed inference streaming.
                        </p>
                    </div>
                )}

                {messages.map((msg, i) => (
                    <Message
                        key={msg.id}
                        role={msg.role}
                        text={msg.text}
                        timestamp={msg.timestamp}
                        tokens={msg.tokens}
                        isStreaming={isStreaming && msg.role === 'assistant' && i === messages.length - 1}
                    />
                ))}

                {error && (
                    <div className={styles.errorBanner}>
                        <span className={styles.errorIcon}>âš </span>
                        <div>
                            <strong>Node Error</strong>
                            <p>{error}</p>
                        </div>
                    </div>
                )}

                <div ref={bottomRef} />
            </main>

            {/* â”€â”€ Input â”€â”€ */}
            <footer className={styles.inputArea}>
                <div className={styles.inputWrapper}>
                    <span className={styles.inputPrefix}>QUERY &gt;</span>
                    <textarea
                        ref={textareaRef}
                        className={styles.textarea}
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Enter query for distributed inferenceâ€¦  (Enter to send Â· Shift+Enter for newline)"
                        rows={1}
                        disabled={isStreaming}
                    />
                </div>

                {isStreaming ? (
                    <button className={`${styles.sendBtn} ${styles.stopBtn}`} onClick={handleStop}>
                        <span className={styles.btnIcon}>â– </span> ABORT
                    </button>
                ) : (
                    <button className={styles.sendBtn} onClick={handleSubmit} disabled={!input.trim()}>
                        <span className={styles.btnIcon}>â–¶</span> SEND
                    </button>
                )}
            </footer>
        </div>
    )
}
