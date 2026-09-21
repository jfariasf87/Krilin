package dev.krilin.bridge

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Detail screen with deliberately no view IDs: Flutter, Compose and web apps expose labels and roles,
 * not resource IDs, so a task must be able to target this screen by description and text alone.
 */
class FixtureDetailActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val note = FixtureState.notes.getOrNull(intent.getIntExtra("index", -1))
        if (note == null) {
            finish()
            return
        }
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            fitsSystemWindows = true // Edge-to-edge on API 35 would hide the first row under the status bar.
            setPadding(32, 48, 32, 32)
        }
        val status = TextView(this)
        fun render() {
            status.setText(if (note.done) R.string.fixture_done else R.string.fixture_not_done)
        }
        layout.addView(TextView(this).apply {
            text = note.title
            textSize = 24f
        })
        layout.addView(status)
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        actions.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.checkbox_on_background)
            contentDescription = getString(R.string.fixture_toggle_done)
            setOnClickListener {
                note.done = !note.done
                render()
            }
        })
        actions.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_menu_delete)
            contentDescription = getString(R.string.fixture_delete_note)
            setOnClickListener {
                AlertDialog.Builder(this@FixtureDetailActivity)
                    .setMessage(getString(R.string.fixture_delete_confirm, note.title))
                    .setPositiveButton(R.string.fixture_delete) { _, _ ->
                        FixtureState.notes.remove(note)
                        finish()
                    }
                    .setNegativeButton(R.string.fixture_cancel, null)
                    .show()
            }
        })
        actions.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_menu_revert)
            contentDescription = getString(R.string.fixture_back_to_notes)
            setOnClickListener { finish() }
        })
        layout.addView(actions)
        setContentView(layout)
        render()
    }
}
