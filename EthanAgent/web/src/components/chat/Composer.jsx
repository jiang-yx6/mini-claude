/**
 * @param {{
 *   value: string,
 *   onChange: (v: string) => void,
 *   onSend: () => void,
 *   disabled?: boolean,
 *   placeholder?: string,
 *   sending?: boolean,
 * }} props
 */
export default function Composer({
  value,
  onChange,
  onSend,
  disabled,
  placeholder,
  sending,
}) {
  return (
    <footer className="composer">
      <textarea
        className="composer-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        rows={2}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            if (!disabled && value.trim()) onSend()
          }
        }}
      />
      <button
        type="button"
        className="composer-send"
        disabled={disabled || !value.trim() || sending}
        onClick={onSend}
      >
        {sending ? '等待…' : '发送'}
      </button>
    </footer>
  )
}
