@file:Suppress("DEPRECATION")

package dev.krilin.bridge

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit

/** Long-lived accessibility driver. Never suppresses or disables other services. */
class BridgeService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var server: ServerSocket? = null
    @Volatile private var running = false
    private var current: Capture? = null
    @Volatile private var generation = 0L
    private val events = Object()
    @Volatile private var lastChange = SystemClock.elapsedRealtime()
    // An accepted action should show some effect before the next observation; see handle().
    @Volatile private var effectPendingSince = -1L
    @Volatile private var generationAtAction = 0L

    private data class Capture(
        val id: String,
        val wire: JSONObject,
        val signature: String,
        val nodes: Map<String, AccessibilityNodeInfo>,
        val generation: Long,
        val created: Long = SystemClock.elapsedRealtime(),
    ) {
        fun release() { nodes.values.forEach { it.recycle() } }
    }

    override fun onServiceConnected() {
        if (running) return
        running = true
        Thread({
            try {
                val listener = ServerSocket(8765, 8, InetAddress.getByName("127.0.0.1"))
                server = listener
                listener.use {
                    while (running) {
                        val socket = listener.accept()
                        socket.use { handle(it) }
                    }
                }
            } catch (_: Exception) {
                // Service restart reopens the socket; the host reports disconnection.
            } finally { running = false }
        }, "krilin-bridge").start()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType !in setOf(
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED,
            AccessibilityEvent.TYPE_VIEW_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED,
            AccessibilityEvent.TYPE_VIEW_SCROLLED,
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED,
        )) return
        generation++
        synchronized(events) {
            lastChange = SystemClock.elapsedRealtime()
            events.notifyAll()
        }
    }

    override fun onInterrupt() { generation++ }

    override fun onDestroy() {
        running = false
        server?.close()
        current?.release()
        current = null
        super.onDestroy()
    }

    private fun handle(socket: Socket) {
        try {
            socket.soTimeout = 2000
            val input = socket.getInputStream()
            val bytes = java.io.ByteArrayOutputStream()
            while (true) {
                val value = input.read()
                if (value == -1) return
                if (value == 10) break
                if (bytes.size() >= 16384) return
                bytes.write(value)
            }
            val request = JSONObject(bytes.toString("UTF-8"))
            val expected = getSharedPreferences("bridge", MODE_PRIVATE).getString("token", "")!!
            val supplied = request.optString("token", "")
            val reply = if (expected.length != 64 || !MessageDigest.isEqual(expected.toByteArray(), supplied.toByteArray())) {
                error("unauthorized")
            } else if (request.optInt("protocol") != 1) {
                error("unsupported_protocol")
            } else {
                val requestDeadline = SystemClock.elapsedRealtime() + request.optLong("timeout_ms", 2000).coerceIn(100, 5000) - 50
                if (request.optString("method") == "observe") {
                    synchronized(events) {
                        // After an accepted action, wait for the first UI event it causes (at most 500ms)
                        // so a dialog or list refresh is not observed before it exists. A no-op action
                        // simply costs the cap.
                        val effectDeadline = minOf(requestDeadline, effectPendingSince + 500)
                        while (effectPendingSince >= 0 && generation == generationAtAction) {
                            val wait = effectDeadline - SystemClock.elapsedRealtime()
                            if (wait <= 0) break
                            events.wait(wait)
                        }
                        effectPendingSince = -1
                        // Then wait for a 150ms quiet window (ViewRootImpl batches content changes every
                        // 100ms), at most 500ms. Never wait indefinitely for an animated screen.
                        // Notifications wake this thread immediately.
                        val settleDeadline = minOf(requestDeadline, SystemClock.elapsedRealtime() + 500)
                        while (true) {
                            val now = SystemClock.elapsedRealtime()
                            val wait = minOf(QUIET_MS - (now - lastChange), settleDeadline - now)
                            if (wait <= 0) break
                            events.wait(wait)
                        }
                    }
                }
                // An action not dispatched within 1.5s has an unknown outcome; an observation has no
                // side effects, so a slow app may take the whole request budget to answer.
                val expires = if (request.optString("method") == "observe") requestDeadline
                    else minOf(requestDeadline, SystemClock.elapsedRealtime() + 1500)
                val task = FutureTask {
                    if (SystemClock.elapsedRealtime() > expires) error("expired_request")
                    else dispatch(request)
                }
                main.post(task)
                try { task.get(maxOf(1, expires - SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS) }
                catch (_: Exception) {
                    task.cancel(false)
                    error("timeout_outcome_unknown")
                }
            }
            socket.getOutputStream().write((reply.toString() + "\n").toByteArray(Charsets.UTF_8))
        } catch (_: Exception) {
            // Invalid requests cannot execute an action; errors never echo payloads.
        }
    }

    private fun dispatch(request: JSONObject): JSONObject = when (request.optString("method")) {
        "observe" -> {
            current?.release()
            val fresh = capture()
            current = fresh
            fresh.wire
        }
        "act" -> act(request)
        else -> error("unknown_method")
    }

    private fun act(request: JSONObject): JSONObject {
        val previous = current ?: return error("stale_snapshot")
        if (request.optString("snapshot_id") != previous.id ||
            SystemClock.elapsedRealtime() - previous.created > 15000) return error("stale_snapshot")
        val fresh = capture()
        try {
            // Events can be redundant. Re-read nodes, geometry and input state;
            // reject actual state changes rather than every event notification.
            if (fresh.signature != previous.signature) return error("stale_snapshot")
            val kind = request.optString("kind")
            val target = request.optString("target", "")
            val ok = if (kind == "back") performGlobalAction(GLOBAL_ACTION_BACK) else {
                val node = fresh.nodes[target] ?: return error("unknown_target")
                if (!node.isEnabled || node.isPassword || !node.refresh()) return error("invalid_target")
                val action = when (kind) {
                    "click" -> AccessibilityNodeInfo.ACTION_CLICK
                    "set_text" -> AccessibilityNodeInfo.ACTION_SET_TEXT
                    "scroll_forward" -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                    "scroll_backward" -> AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                    else -> return error("unsupported_action")
                }
                if (node.actionList.none { it.id == action }) return error("unsupported_action")
                val args = if (kind == "set_text") {
                    if (!request.has("text") || request.isNull("text")) return error("missing_text")
                    val text = request.getString("text")
                    if (text.length > 2000) return error("text_too_long")
                    Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
                } else null
                node.performAction(action, args)
            }
            if (ok) {
                generationAtAction = generation
                effectPendingSince = SystemClock.elapsedRealtime()
            }
            return if (ok) JSONObject().put("ok", true) else error("action_rejected")
        } finally {
            fresh.release()
            previous.release()
            current = null // Every action token is single-use, including failed attempts.
            synchronized(events) {
                // Allow effects to reach Android's accessibility cache before observing.
                lastChange = SystemClock.elapsedRealtime()
                events.notifyAll()
            }
        }
    }

    private fun capture(): Capture {
        val elements = JSONArray()
        val geometry = JSONArray()
        val nodes = linkedMapOf<String, AccessibilityNodeInfo>()
        val manager = getSystemService(ACCESSIBILITY_SERVICE) as AccessibilityManager
        val services = manager.getEnabledAccessibilityServiceList(-1).map { it.id }.sorted()
        val windowList = windows
        val root = rootInActiveWindow
        val activePackage = root?.packageName?.toString() ?: ""
        root?.recycle()
        var visited = 0
        var truncated = false
        fun visit(node: AccessibilityNodeInfo, path: String, depth: Int) {
            try {
                if (++visited > 2048 || depth > 64 || elements.length() >= 512) {
                    truncated = true
                    return
                }
                if (!node.isVisibleToUser) return
                val supported = node.actionList.map { it.id }.toSet()
                val actions = JSONArray()
                if (node.isEnabled && !node.isPassword) {
                    listOf(
                        AccessibilityNodeInfo.ACTION_CLICK to "click",
                        AccessibilityNodeInfo.ACTION_SET_TEXT to "set_text",
                        AccessibilityNodeInfo.ACTION_SCROLL_FORWARD to "scroll_forward",
                        AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD to "scroll_backward",
                    ).forEach { (code, name) -> if (code in supported) actions.put(name) }
                }
                val rawText = if (node.isPassword || node.isShowingHintText) "" else node.text?.toString().orEmpty()
                val rawDescription = if (node.isPassword) "" else (node.contentDescription ?: node.hintText)?.toString().orEmpty()
                if (rawText.length > 2000 || rawDescription.length > 2000) truncated = true
                val text = rawText.take(2000)
                val description = rawDescription.take(2000)
                if (actions.length() > 0 || text.isNotEmpty() || description.isNotEmpty()) {
                    val id = "e${elements.length() + 1}"
                    nodes[id] = AccessibilityNodeInfo.obtain(node)
                    elements.put(JSONObject()
                        .put("id", id).put("package", node.packageName?.toString().orEmpty())
                        .put("resource_id", node.viewIdResourceName.orEmpty())
                        .put("text", text).put("description", description)
                        .put("role", node.className?.toString().orEmpty())
                        .put("enabled", node.isEnabled).put("checked", node.isChecked)
                        .put("focused", node.isFocused).put("password", node.isPassword)
                        .put("actions", actions))
                    val bounds = Rect().also { node.getBoundsInScreen(it) }
                    geometry.put("$path:${node.windowId}:$bounds:${node.isAccessibilityFocused}")
                }
                for (i in 0 until node.childCount) {
                    if (visited >= 2048 || elements.length() >= 512) { truncated = true; break }
                    node.getChild(i)?.let { visit(it, "$path/$i", depth + 1) }
                }
            } finally { node.recycle() }
        }
        windowList.sortedByDescending { it.layer }.forEach { window ->
            window.root?.let { visit(it, "w${window.id}", 0) }
        }
        if (windowList.isEmpty()) rootInActiveWindow?.let { visit(it, "active", 0) }
        val input = JSONObject().put("execution_mode", "semantic")
            .put("touch_exploration", manager.isTouchExplorationEnabled)
            .put("accessibility_services", JSONArray(services))
            .put("ime_visible", windowList.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD })
        val id = UUID.randomUUID().toString()
        val wire = JSONObject().put("protocol", 1).put("elements", elements)
            .put("input", input).put("active_package", activePackage).put("truncated", truncated)
        val signature = wire.toString() + geometry.toString()
        wire.put("snapshot_id", id)
        return Capture(id, wire, signature, nodes, generation)
    }

    private fun error(code: String) = JSONObject().put("error", code)

    private companion object {
        const val QUIET_MS = 150L
    }
}
