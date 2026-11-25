"""Domain packs: everything that is specific to one protected application.

    domains/<name>/tools.yaml    the application's tools and the deny rules for each
    domains/<name>/trust.yaml    its content sources and their trust classes
    domains/<name>/domain.yaml   the application described in words (for prompts and messages)
    domains/<name>/resolver.py   an adapter that answers the engine's questions about the
                                 application's records (who owns this order?)

The engine (guardrail/) loads a pack by name and never imports an application directly.
"""
