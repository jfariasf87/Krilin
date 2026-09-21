package dev.krilin.bridge

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.ProgressBar
import android.widget.TextView

/**
 * Offline, deterministic screen with the UI patterns that make app testing hard: a modal that must be
 * dismissed first, delayed content, a form with validation, a filter toggle, a long list whose targets
 * are off-screen, and navigation to an ID-less detail screen. No other app's data is touched.
 */
class FixtureActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var summary: TextView
    private lateinit var sync: Button
    private lateinit var progress: ProgressBar
    private lateinit var syncStatus: TextView
    private lateinit var adapter: ArrayAdapter<String>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) FixtureState.reset()
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            fitsSystemWindows = true // Edge-to-edge on API 35 would hide the first row under the status bar.
            setPadding(32, 48, 32, 32)
        }
        summary = TextView(this).apply { id = R.id.fixture_summary }
        layout.addView(summary)
        layout.addView(CheckBox(this).apply {
            id = R.id.fixture_hide_done
            setText(R.string.fixture_hide_done)
            isChecked = FixtureState.hideDone
            setOnCheckedChangeListener { _, checked ->
                FixtureState.hideDone = checked
                refresh()
            }
        })
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        sync = Button(this).apply {
            id = R.id.fixture_sync
            setText(R.string.fixture_sync)
            setOnClickListener { startSync() }
        }
        progress = ProgressBar(this).apply {
            id = R.id.fixture_progress
            contentDescription = getString(R.string.fixture_syncing)
            visibility = View.GONE
        }
        actions.addView(sync)
        actions.addView(Button(this).apply {
            id = R.id.fixture_add
            setText(R.string.fixture_add_note)
            setOnClickListener { showAddDialog() }
        })
        actions.addView(progress)
        layout.addView(actions)
        syncStatus = TextView(this).apply { id = R.id.fixture_sync_status }
        layout.addView(syncStatus)
        adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, mutableListOf<String>())
        layout.addView(ListView(this).apply {
            id = R.id.fixture_list
            adapter = this@FixtureActivity.adapter
            setOnItemClickListener { _, _, position, _ ->
                val note = FixtureState.visible()[position]
                startActivity(Intent(this@FixtureActivity, FixtureDetailActivity::class.java)
                    .putExtra("index", FixtureState.notes.indexOf(note)))
            }
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(layout)
        refresh()
        if (savedInstanceState == null) showWhatsNew()
    }

    override fun onResume() {
        super.onResume()
        refresh() // The detail screen may have changed or removed a note.
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun refresh() {
        summary.text = getString(R.string.fixture_summary, FixtureState.notes.size, FixtureState.notes.count { it.done })
        adapter.clear()
        adapter.addAll(FixtureState.visible().map {
            if (it.done) getString(R.string.fixture_note_done, it.title) else it.title
        })
    }

    private fun startSync() {
        sync.isEnabled = false
        syncStatus.text = ""
        progress.visibility = View.VISIBLE
        main.postDelayed({
            progress.visibility = View.GONE
            sync.isEnabled = true
            syncStatus.text = getString(R.string.fixture_synced, FixtureState.notes.size)
        }, 1500)
    }

    private fun showWhatsNew() {
        AlertDialog.Builder(this)
            .setTitle(R.string.fixture_whats_new)
            .setMessage(R.string.fixture_whats_new_body)
            .setPositiveButton(R.string.fixture_got_it, null)
            .setCancelable(false)
            .show()
    }

    private fun showAddDialog() {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val title = EditText(this).apply {
            id = R.id.fixture_title
            hint = getString(R.string.fixture_title_hint)
            isSingleLine = true
        }
        val error = TextView(this).apply {
            id = R.id.fixture_title_error
            visibility = View.GONE
        }
        content.addView(title)
        content.addView(error)
        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.fixture_add_note)
            .setView(content)
            .setPositiveButton(R.string.fixture_add, null)
            .setNegativeButton(R.string.fixture_cancel, null)
            .create()
        dialog.setOnShowListener {
            // Validation keeps the dialog open; the default listener would dismiss it.
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val value = title.text.toString().trim()
                if (value.isEmpty()) {
                    error.setText(R.string.fixture_title_required)
                    error.visibility = View.VISIBLE
                } else {
                    FixtureState.notes.add(FixtureState.Note(value, false))
                    refresh()
                    dialog.dismiss()
                }
            }
        }
        dialog.show()
    }
}
