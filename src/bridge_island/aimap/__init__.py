"""The ``ai-mapping`` experiment: can a model propose the Bridge's mapping for a new version?

A model receives the old and new baseline interface, the unique systems' declared needs and the old
mapping, and returns a mapping for the new version with a confidence for each entry. The existing
contract check (``codegen.generate``) accepts or rejects it, and the accepted proposals are scored
against the hand-written mapping by *behaviour*. Everything is invented; live model calls send that
invented content to the provider you choose, and replay mode (the default) needs no network.
"""
