package dev.krilin.bridge

/** Deterministic in-memory data for the complexity fixture. Reset on every fresh launch of the list screen. */
object FixtureState {
    class Note(val title: String, var done: Boolean)

    val notes = mutableListOf<Note>()
    var hideDone = false

    fun reset() {
        notes.clear()
        for (i in 1..30) notes.add(Note("Note %02d".format(i), i == 3 || i == 7 || i == 11))
        hideDone = false
    }

    fun visible(): List<Note> = if (hideDone) notes.filter { !it.done } else notes.toList()
}
