from paperpush.models import Author, Paper
from paperpush.query import evaluate, parse_query, validate_query


PAPER = Paper(
    doi="10.test/1",
    title="Optogenetic control of hippocampal memory",
    journal="Nature Neuroscience",
    authors=[Author("Jane", "Doe", "first"), Author("Susumu", "Tonegawa", "last")],
    abstract="Synaptic plasticity in a neural circuit.",
)


def test_boolean_precedence_and_parentheses():
    assert evaluate(parse_query("astrocyte OR optogenetic AND memory"), PAPER).matched
    assert not evaluate(parse_query("(astrocyte OR optogenetic) AND microglia"), PAPER).matched


def test_fields_and_wildcards():
    query = 'TS=(optogenetic* AND memory) AND LA=(Tonegawa OR Buzsaki) AND NOT SO=Neuron'
    assert validate_query(query)["valid"]
    assert evaluate(parse_query(query), PAPER).matched


def test_invalid_query():
    assert not validate_query("(A OR B")["valid"]
