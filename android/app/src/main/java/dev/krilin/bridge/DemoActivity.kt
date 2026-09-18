package dev.krilin.bridge

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** An isolated test fixture: no other app's data is touched by smoke tests. */
class DemoActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 80, 32, 32)
        }
        val name = EditText(this).apply {
            id = R.id.demo_name
            hint = getString(R.string.name_hint)
            contentDescription = getString(R.string.name_hint)
            isSingleLine = true
        }
        val status = TextView(this).apply {
            id = R.id.demo_status
            setText(R.string.not_saved)
        }
        layout.addView(name)
        layout.addView(Button(this).apply {
            id = R.id.demo_save
            setText(R.string.save)
            setOnClickListener { status.text = getString(R.string.saved_value, name.text) }
        })
        layout.addView(status)
        setContentView(layout)
    }
}
