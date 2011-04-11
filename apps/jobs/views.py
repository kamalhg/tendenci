from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse
from django.contrib import messages

from base.http import Http403
from base.utils import now_localized

from jobs.models import Job, JobPricing
from jobs.forms import JobForm, JobPricingForm
from jobs.utils import job_set_inv_payment

from event_logs.models import EventLog
from meta.models import Meta as MetaTags
from meta.forms import MetaForm
from site_settings.utils import get_setting

from perms.utils import get_notice_recipients, is_admin, update_perms_and_save, has_perm


try:
    from notification import models as notification
except:
    notification = None


def index(request, slug=None, template_name="jobs/view.html"):
    if not slug:
        return HttpResponseRedirect(reverse('job.search'))
    job = get_object_or_404(Job, slug=slug)

    # non-admin can not view the non-active content
    # status=0 has been taken care of in the has_perm function
    if (job.status_detail).lower() != 'active' and (not is_admin(request.user)):
        raise Http403

    if has_perm(request.user, 'jobs.view_job', job):
        log_defaults = {
            'event_id': 255000,
            'event_data': '%s (%d) viewed by %s' % (
                 job._meta.object_name,
                 job.pk, request.user
            ),
            'description': '%s viewed' % job._meta.object_name,
            'user': request.user,
            'request': request,
            'instance': job,
        }
        EventLog.objects.log(**log_defaults)
        return render_to_response(template_name, {'job': job},
            context_instance=RequestContext(request))
    else:
        raise Http403


def search(request, template_name="jobs/search.html"):
    query = request.GET.get('q', None)
    jobs = Job.objects.search(query, user=request.user)
    jobs = jobs.order_by('-create_dt')

    log_defaults = {
        'event_id': 254000,
        'event_data': '%s searched by %s' % ('Job', request.user),
        'description': '%s searched' % 'Job',
        'user': request.user,
        'request': request,
        'source': 'jobs'
    }
    EventLog.objects.log(**log_defaults)

    return render_to_response(template_name, {'jobs': jobs},
        context_instance=RequestContext(request))


def print_view(request, slug, template_name="jobs/print-view.html"):
    job = get_object_or_404(Job, slug=slug)

    log_defaults = {
        'event_id': 255001,
        'event_data': '%s (%d) viewed by %s' % (job._meta.object_name, job.pk, request.user),
        'description': '%s viewed - print view' % job._meta.object_name,
        'user': request.user,
        'request': request,
        'instance': job,
    }
    EventLog.objects.log(**log_defaults)

    if has_perm(request.user, 'jobs.view_job', job):
        return render_to_response(template_name, {'job': job},
            context_instance=RequestContext(request))
    else:
        raise Http403


def add(request, form_class=JobForm, template_name="jobs/add.html"):
    require_payment = get_setting('module', 'jobs', 'jobsrequirespayment')

    user_is_authenticated = request.user.is_authenticated()
    user_is_admin = is_admin(request.user)
    can_add_active = user_is_authenticated or user_is_admin

    if request.method == "POST":
        form = form_class(request.POST, user=request.user)

        # adjust the fields depending on user type
        if not require_payment:
            del form.fields['payment_method']
            del form.fields['list_type']

        if form.is_valid():
            job = form.save(commit=False)

            # set it to pending if the user is anonymous or not an admin
            if not can_add_active:
                job.status = 0
                job.status_detail = 'pending'

            # list types and duration
            if not job.requested_duration:
                job.requested_duration = 30
            if not job.list_type:
                job.list_type = 'regular'

            # set up all the times
            now = now_localized()
            job.activation_dt = now
            if not job.post_dt:
                job.post_dt = now

            # set the expiration date
            job.expiration_dt = job.activation_dt + timedelta(days=job.requested_duration)

            job = update_perms_and_save(request, form, job)

            # create invoice
            job_set_inv_payment(request.user, job)

            log_defaults = {
                'event_id': 251000,
                'event_data': '%s (%d) added by %s' % (job._meta.object_name, job.pk, request.user),
                'description': '%s added' % job._meta.object_name,
                'user': request.user,
                'request': request,
                'instance': job,
            }
            EventLog.objects.log(**log_defaults)

            if user_is_authenticated:
                messages.add_message(request, messages.INFO, 'Successfully added %s' % job)

            # send notification to administrators
            recipients = get_notice_recipients('module', 'jobs', 'jobrecipients')
            if recipients:
                if notification:
                    extra_context = {
                        'object': job,
                        'request': request,
                    }
                    notification.send_emails(recipients, 'job_added', extra_context)

            # send user to the payment page if payment is required
            if require_payment:
                if job.payment_method.lower() in ['credit card', 'cc']:
                    if job.invoice and job.invoice.balance > 0:
                        return HttpResponseRedirect(reverse(
                            'payments.views.pay_online',
                            args=[job.invoice.id, job.invoice.guid])
                        )

            # send user to thank you or view page
            if not user_is_admin:
                return HttpResponseRedirect(reverse('job.thank_you'))
            else:
                return HttpResponseRedirect(reverse('job', args=[job.slug]))
    else:
        form = form_class(user=request.user)

        # adjust the fields depending on user type
        if not require_payment:
            del form.fields['payment_method']
            del form.fields['list_type']

    return render_to_response(template_name, {'form': form},
        context_instance=RequestContext(request))


@login_required
def edit(request, id, form_class=JobForm, template_name="jobs/edit.html"):
    job = get_object_or_404(Job, pk=id)

    if has_perm(request.user, 'jobs.change_job', job):
        if request.method == "POST":
            form = form_class(request.POST, instance=job, user=request.user)

            # delete admin only fields for non-admin on edit - GJQ 8/25/2010
            if not is_admin(request.user):
                del form.fields['requested_duration']
                del form.fields['list_type']
                del form.fields['activation_dt']
                del form.fields['post_dt']
                del form.fields['expiration_dt']
                del form.fields['entity']
            del form.fields['payment_method']

            if form.is_valid():
                job = form.save(commit=False)

                job = update_perms_and_save(request, form, job)

                log_defaults = {
                    'event_id': 252000,
                    'event_data': '%s (%d) edited by %s' % (job._meta.object_name, job.pk, request.user),
                    'description': '%s edited' % job._meta.object_name,
                    'user': request.user,
                    'request': request,
                    'instance': job,
                }
                EventLog.objects.log(**log_defaults)

                messages.add_message(request, messages.INFO, 'Successfully updated %s' % job)

                return HttpResponseRedirect(reverse('job', args=[job.slug]))
        else:
            form = form_class(instance=job, user=request.user)
            if not is_admin(request.user):
                del form.fields['requested_duration']
                del form.fields['list_type']
                del form.fields['activation_dt']
                del form.fields['post_dt']
                del form.fields['expiration_dt']
                del form.fields['entity']
            del form.fields['payment_method']

        return render_to_response(template_name, {'job': job, 'form': form},
            context_instance=RequestContext(request))
    else:
        raise Http403


@login_required
def edit_meta(request, id, form_class=MetaForm, template_name="jobs/edit-meta.html"):

    # check permission
    job = get_object_or_404(Job, pk=id)
    if not has_perm(request.user, 'jobs.change_job', job):
        raise Http403

    defaults = {
        'title': job.get_title(),
        'description': job.get_description(),
        'keywords': job.get_keywords(),
        'canonical_url': job.get_canonical_url(),
    }
    job.meta = MetaTags(**defaults)

    if request.method == "POST":
        form = form_class(request.POST, instance=job.meta)
        if form.is_valid():
            job.meta = form.save()  # save meta
            job.save()  # save relationship

            messages.add_message(request, messages.INFO, 'Successfully updated meta for %s' % job)

            return HttpResponseRedirect(reverse('job', args=[job.slug]))
    else:
        form = form_class(instance=job.meta)

    return render_to_response(template_name, {'job': job, 'form': form},
        context_instance=RequestContext(request))


@login_required
def delete(request, id, template_name="jobs/delete.html"):
    job = get_object_or_404(Job, pk=id)

    if has_perm(request.user, 'jobs.delete_job'):
        if request.method == "POST":
            log_defaults = {
                'event_id': 433000,
                'event_data': '%s (%d) deleted by %s' % (job._meta.object_name, job.pk, request.user),
                'description': '%s deleted' % job._meta.object_name,
                'user': request.user,
                'request': request,
                'instance': job,
            }

            EventLog.objects.log(**log_defaults)
            messages.add_message(request, messages.INFO, 'Successfully deleted %s' % job)

            # send notification to administrators
            recipients = get_notice_recipients('module', 'jobs', 'jobrecipients')
            if recipients:
                if notification:
                    extra_context = {
                        'object': job,
                        'request': request,
                    }
                    notification.send_emails(recipients, 'job_deleted', extra_context)

            job.delete()

            return HttpResponseRedirect(reverse('job.search'))

        return render_to_response(template_name, {'job': job},
            context_instance=RequestContext(request))
    else:
        raise Http403


@login_required
def pricing_add(request, form_class=JobPricingForm, template_name="jobs/pricing-add.html"):
    if has_perm(request.user, 'jobs.add_jobpricing'):
        if request.method == "POST":
            form = form_class(request.POST)
            if form.is_valid():
                job_pricing = form.save(commit=False)
                job_pricing.status = 1
                job_pricing.save(request.user)

                log_defaults = {
                    'event_id': 265100,
                    'event_data': '%s (%d) added by %s' % (
                        job_pricing._meta.object_name,
                        job_pricing.pk,
                        request.user
                    ),
                    'description': '%s added' % job_pricing._meta.object_name,
                    'user': request.user,
                    'request': request,
                    'instance': job_pricing,
                }
                EventLog.objects.log(**log_defaults)

                return HttpResponseRedirect(reverse('job_pricing.view', args=[job_pricing.id]))
        else:
            form = form_class()

        return render_to_response(template_name, {'form': form},
            context_instance=RequestContext(request))
    else:
        raise Http403


@login_required
def pricing_edit(request, id, form_class=JobPricingForm, template_name="jobs/pricing-edit.html"):
    job_pricing = get_object_or_404(JobPricing, pk=id)
    if not has_perm(request.user, 'jobs.change_jobpricing', job_pricing):
        Http403

    if request.method == "POST":
        form = form_class(request.POST, instance=job_pricing)
        if form.is_valid():
            job_pricing = form.save(commit=False)
            job_pricing.save(request.user)

            log_defaults = {
                'event_id': 265110,
                'event_data': '%s (%d) edited by %s' % (
                    job_pricing._meta.object_name,
                    job_pricing.pk,
                    request.user
                ),
                'description': '%s edited' % job_pricing._meta.object_name,
                'user': request.user,
                'request': request,
                'instance': job_pricing,
            }
            EventLog.objects.log(**log_defaults)

            return HttpResponseRedirect(reverse(
                'job_pricing.view',
                args=[job_pricing.id])
            )
    else:
        form = form_class(instance=job_pricing)

    return render_to_response(template_name, {'form': form},
        context_instance=RequestContext(request))


@login_required
def pricing_view(request, id, template_name="jobs/pricing-view.html"):
    job_pricing = get_object_or_404(JobPricing, id=id)

    if has_perm(request.user, 'jobs.add_jobpricing', job_pricing):
        return render_to_response(template_name, {'job_pricing': job_pricing},
            context_instance=RequestContext(request))
    else:
        raise Http403


@login_required
def pricing_delete(request, id, template_name="jobs/pricing-delete.html"):
    job_pricing = get_object_or_404(JobPricing, pk=id)

    if not has_perm(request.user, 'jobs.delete_jobpricing'):
        raise Http403

    if request.method == "POST":
        log_defaults = {
            'event_id': 265120,
            'event_data': '%s (%d) deleted by %s' % (
                job_pricing._meta.object_name,
                job_pricing.pk,
                request.user
            ),
            'description': '%s deleted' % job_pricing._meta.object_name,
            'user': request.user,
            'request': request,
            'instance': job_pricing,
        }

        EventLog.objects.log(**log_defaults)
        messages.add_message(request, messages.INFO, 'Successfully deleted %s' % job_pricing)

        job_pricing.delete()

        return HttpResponseRedirect(reverse('job_pricing.search'))

    return render_to_response(template_name, {'job_pricing': job_pricing},
        context_instance=RequestContext(request))


def pricing_search(request, template_name="jobs/pricing-search.html"):
    job_pricings = JobPricing.objects.all().order_by('duration')

    return render_to_response(template_name, {'job_pricings': job_pricings},
        context_instance=RequestContext(request))


def pending(request, template_name="jobs/pending.html"):
    if not is_admin(request.user):
        raise Http403
    jobs = Job.objects.filter(status=0, status_detail='pending')
    return render_to_response(template_name, {'jobs': jobs},
            context_instance=RequestContext(request))


def approve(request, id, template_name="jobs/approve.html"):
    if not is_admin(request.user):
        raise Http403
    job = get_object_or_404(Job, pk=id)

    if request.method == "POST":
        job.activation_dt = now_localized()
        job.allow_anonymous_view = True
        job.status = True
        job.status_detail = 'active'

        if not job.creator:
            job.creator = request.user
            job.creator_username = request.user.username

        if not job.owner:
            job.owner = request.user
            job.owner_username = request.user.username

        job.save()

        messages.add_message(request, messages.INFO, 'Successfully approved %s' % job)

        return HttpResponseRedirect(reverse('job', args=[job.slug]))

    return render_to_response(template_name, {'job': job},
            context_instance=RequestContext(request))


def thank_you(request, template_name="jobs/thank-you.html"):
    return render_to_response(template_name, {}, context_instance=RequestContext(request))
