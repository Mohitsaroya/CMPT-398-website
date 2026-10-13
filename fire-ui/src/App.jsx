import { useEffect, useRef, useState } from 'react'

export default function App() {
  const canvasRef = useRef(null)
  const [status, setStatus] = useState('Waiting for frames…')

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    let pendingFrame = null
    let decoding = false
    let stopped = false

    const drawLatestFrame = () => {
      if (decoding || !pendingFrame || stopped) return
      const d = pendingFrame
      pendingFrame = null
      const img = new Image()
      decoding = true
      img.onload = () => {
        if (stopped) return
        if (pendingFrame) {
          decoding = false
          drawLatestFrame()
          return
        }

        canvas.width = img.width
        canvas.height = img.height
        ctx.drawImage(img, 0, 0)
        ctx.lineWidth = 2
        ctx.font = '14px sans-serif'

        d.detections.forEach(({ label, conf, box: [x1, y1, x2, y2] }) => {
          ctx.strokeStyle = ctx.fillStyle = label === 'fire' ? 'red' : 'orange'
          ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
          ctx.fillText(`${label} ${(conf * 100).toFixed(0)}%`, x1, Math.max(14, y1 - 4))
        })

        if (d.detections.length) {
          const top = d.detections.reduce((a, b) => (a.conf > b.conf ? a : b))
          setStatus(`${top.label.toUpperCase()} detected, ${(top.conf * 100).toFixed(0)}%`)
        } else {
          setStatus('No fire detected')
        }
        decoding = false
        drawLatestFrame()
      }
      img.onerror = () => {
        decoding = false
        console.error('Failed to decode incoming video frame')
        drawLatestFrame()
      }
      img.src = 'data:image/jpeg;base64,' + d.image
    }

    ws.onmessage = (e) => {
      pendingFrame = JSON.parse(e.data)
      drawLatestFrame()
    }

    return () => {
      stopped = true
      ws.close()
    }
  }, [])

  return (
    <div>
      <h2>{status}</h2>
      <canvas ref={canvasRef} style={{ width: '100%', maxWidth: 900 }} />
    </div>
  )
}