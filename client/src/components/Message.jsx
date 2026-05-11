import styles from './Message.module.css'

function formatTime(date) {
    if (!date) return ''
    return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    })
}

export default function Message({ role, text, timestamp, tokens, isStreaming }) {
    const isUser = role === 'user'

    return (
        <div className={`${styles.row} ${isUser ? styles.userRow : styles.assistantRow}`}>
            {/* Node label above bubble */}
            <div className={styles.nodeTag}>
                <span className={styles.nodeDot} data-role={role} />
                <span className={styles.nodeLabel}>
                    {isUser ? 'CLIENT NODE' : 'COORDINATOR'}
                </span>
            </div>

            {/* Message bubble */}
            <div className={`${styles.bubble} ${isUser ? styles.userBubble : styles.assistantBubble}`}>
                {text || null}
                {(isStreaming || !text) && <span className={styles.cursor} />}
            </div>

            {/* Meta: timestamp + token count */}
            <div className={styles.meta}>
                {timestamp && (
                    <span className={styles.metaTime}>{formatTime(timestamp)}</span>
                )}
                {!isUser && tokens != null && tokens > 0 && (
                    <span className={styles.metaTokens}>{tokens} tkns</span>
                )}
            </div>
        </div>
    )
}
