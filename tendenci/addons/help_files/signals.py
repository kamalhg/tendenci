def init_signals():
    from django.db.models.signals import post_save
    from tendenci.addons.help_files.models import HelpFile
    from tendenci.apps.contributions.signals import save_contribution

    post_save.connect(save_contribution, sender=HelpFile, weak=False)