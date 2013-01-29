import logging
import random

from django.conf import settings

from django.http import (HttpResponseRedirect, HttpResponse,
        HttpResponseForbidden, HttpResponseNotFound, Http404)

from django.utils import simplejson

from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext
from django.template.defaultfilters import slugify

try:
    from funfactory.urlresolvers import (get_url_prefix, Prefixer, reverse,
                                         set_url_prefix)
    from tower import activate
except ImportError, e:
    from django.core.urlresolvers import reverse

try:
    from tower import ugettext_lazy as _
except ImportError, e:
    from django.utils.translation import ugettext_lazy as _

from django.views.generic.list_detail import object_list
from django.views.decorators.http import (require_GET, require_POST,
                                          require_http_methods)

from django.contrib import messages

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

try:
    import taggit
    from taggit.models import Tag, TaggedItem
except:
    taggit = None

from .models import (Badge, Award, Nomination, DeferredAward,
                     Progress, BadgeAwardNotAllowedException,
                     BadgeAlreadyAwardedException,
                     NominationApproveNotAllowedException,
                     NominationAcceptNotAllowedException)
from .forms import (BadgeAwardForm, DeferredAwardGrantForm,
                    DeferredAwardMultipleGrantForm, BadgeNewForm,
                    BadgeEditForm, BadgeSubmitNominationForm)

BADGE_PAGE_SIZE = 20
MAX_RECENT = 15
TEMPLATE_BASE = getattr(settings, 'BADGER_TEMPLATE_BASE', 'badger')


def home(request):
    """Badger home page"""
    badge_list = Badge.objects.order_by('-modified').all()[:MAX_RECENT]
    award_list = Award.objects.order_by('-modified').all()[:MAX_RECENT]
    badge_tags = Badge.objects.top_tags()

    return render_to_response('%s/home.html' % TEMPLATE_BASE, dict(
        badge_list=badge_list, award_list=award_list, badge_tags=badge_tags
    ), context_instance=RequestContext(request))


def badges_list(request, tag_name=None):
    """Badges list page"""
    award_list = None
    query_string = request.GET.get('q', None)
    if query_string is not None:
        sort_order = request.GET.get('sort', 'created')
        queryset = Badge.objects.search(query_string, sort_order)
        # TODO: Is this the most efficient query?
        award_list = (Award.objects.filter(badge__in=queryset))
    elif taggit and tag_name:
        tag = get_object_or_404(Tag, name=tag_name)
        queryset = (Badge.objects.filter(tags__in=[tag]).distinct())
        # TODO: Is this the most efficient query?
        award_list = (Award.objects.filter(badge__in=queryset))
    else:
        queryset = Badge.objects.order_by('-modified').all()
    return object_list(request, queryset,
        paginate_by=BADGE_PAGE_SIZE, allow_empty=True,
        extra_context=dict(
            tag_name=tag_name,
            query_string=query_string,
            award_list=award_list,
        ),
        template_object_name='badge',
        template_name='%s/badges_list.html' % TEMPLATE_BASE)


@require_http_methods(['HEAD', 'GET', 'POST'])
def detail(request, slug, format="html"):
    """Badge detail view"""
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_detail_by(request.user):
        return HttpResponseForbidden('Detail forbidden')

    awards = (Award.objects.filter(badge=badge)
                           .order_by('-created'))[:MAX_RECENT]

    # FIXME: This is awkward. It used to collect sections as responses to a
    # signal sent out to badger_multiplayer and hypothetical future expansions
    # to badger
    sections = dict()
    sections['award'] = dict(form=BadgeAwardForm())
    if badge.allows_nominate_for(request.user):
        sections['nominate'] = dict(form=BadgeSubmitNominationForm())

    if request.method == "POST":

        if request.POST.get('is_generate', None):
            if not badge.allows_manage_deferred_awards_by(request.user):
                return HttpResponseForbidden('Claim generate denied')
            amount = int(request.POST.get('amount', 10))
            reusable = (amount == 1)
            cg = badge.generate_deferred_awards(user=request.user,
                                                amount=amount,
                                                reusable=reusable)

        if request.POST.get('is_delete', None):
            if not badge.allows_manage_deferred_awards_by(request.user):
                return HttpResponseForbidden('Claim delete denied')
            group = request.POST.get('claim_group')
            badge.delete_claim_group(request.user, group)

        url = reverse('badger.views.detail', kwargs=dict(slug=slug))
        return HttpResponseRedirect(url)

    claim_groups = badge.claim_groups

    if format == 'json':
        data = badge.as_obi_serialization(request)
        resp = HttpResponse(simplejson.dumps(data))
        resp['Content-Type'] = 'application/json'
        return resp
    else:
        return render_to_response('%s/badge_detail.html' % TEMPLATE_BASE, dict(
            badge=badge, award_list=awards, sections=sections,
            claim_groups=claim_groups
        ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def create(request):
    """Create a new badge"""
    if not Badge.objects.allows_add_by(request.user):
        return HttpResponseForbidden()

    if request.method != "POST":
        form = BadgeNewForm()
        form.initial['tags'] = request.GET.get('tags', '')
    else:
        form = BadgeNewForm(request.POST, request.FILES)
        if form.is_valid():
            new_sub = form.save(commit=False)
            new_sub.creator = request.user
            new_sub.save()
            form.save_m2m()
            return HttpResponseRedirect(reverse(
                    'badger.views.detail', args=(new_sub.slug,)))

    return render_to_response('%s/badge_create.html' % TEMPLATE_BASE, dict(
        form=form,
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def edit(request, slug):
    """Edit an existing badge"""
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_edit_by(request.user):
        return HttpResponseForbidden()

    if request.method != "POST":
        form = BadgeEditForm(instance=badge)
    else:
        form = BadgeEditForm(request.POST, request.FILES, instance=badge)
        if form.is_valid():
            new_sub = form.save(commit=False)
            new_sub.save()
            form.save_m2m()
            return HttpResponseRedirect(reverse(
                    'badger.views.detail', args=(new_sub.slug,)))

    return render_to_response('%s/badge_edit.html' % TEMPLATE_BASE, dict(
        badge=badge, form=form,
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def delete(request, slug):
    """Delete a badge"""
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_delete_by(request.user):
        return HttpResponseForbidden()

    awards_count = badge.award_set.count()

    if request.method == "POST":
        messages.info(request, _('Badge "%s" deleted.') % badge.title)
        badge.delete()
        return HttpResponseRedirect(reverse('badger.views.badges_list'))

    return render_to_response('%s/badge_delete.html' % TEMPLATE_BASE, dict(
        badge=badge, awards_count=awards_count,
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def award_badge(request, slug):
    """Issue an award for a badge"""
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_award_to(request.user):
        return HttpResponseForbidden('Award forbidden')

    if request.method != "POST":
        form = BadgeAwardForm()
    else:
        form = BadgeAwardForm(request.POST, request.FILES)
        if form.is_valid():
            emails = form.cleaned_data['emails']
            description = form.cleaned_data['description']
            for email in emails:
                result = badge.award_to(email=email, awarder=request.user,
                                        description=description)
                if result:
                    if not hasattr(result, 'claim_code'):
                        messages.info(request, _('Award issued to %s') % email)
                    else:
                        messages.info(request, _('Invitation to claim award '
                                                 'sent to %s') % email)
            return HttpResponseRedirect(reverse('badger.views.detail', 
                                                args=(badge.slug,)))

    return render_to_response('%s/badge_award.html' % TEMPLATE_BASE, dict(
        form=form, badge=badge,
    ), context_instance=RequestContext(request))


@require_GET
def awards_list(request, slug=None):
    queryset = Award.objects
    if not slug:
        badge = None
    else:
        badge = get_object_or_404(Badge, slug=slug)
        queryset = queryset.filter(badge=badge)
    queryset = queryset.order_by('-modified').all()

    return object_list(request, queryset,
        paginate_by=BADGE_PAGE_SIZE, allow_empty=True,
        extra_context=dict(
            badge=badge
        ),
        template_object_name='award',
        template_name='%s/awards_list.html' % TEMPLATE_BASE)


@require_http_methods(['HEAD', 'GET'])
def award_detail(request, slug, id, format="html"):
    """Award detail view"""
    badge = get_object_or_404(Badge, slug=slug)
    award = get_object_or_404(Award, badge=badge, pk=id)
    if not award.allows_detail_by(request.user):
        return HttpResponseForbidden('Award detail forbidden')

    if format == 'json':
        data = simplejson.dumps(award.as_obi_assertion(request))
        resp = HttpResponse(data)
        resp['Content-Type'] = 'application/json'
        return resp
    else:
        return render_to_response('%s/award_detail.html' % TEMPLATE_BASE, dict(
            badge=badge, award=award,
        ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def award_delete(request, slug, id):
    """Delete an award"""
    badge = get_object_or_404(Badge, slug=slug)
    award = get_object_or_404(Award, badge=badge, pk=id)
    if not award.allows_delete_by(request.user):
        return HttpResponseForbidden('Award delete forbidden')

    if request.method == "POST":
        messages.info(request, _('Award for badge "%s" deleted.') %
                               badge.title)
        award.delete()
        url = reverse('badger.views.detail', kwargs=dict(slug=slug))
        return HttpResponseRedirect(url)

    return render_to_response('%s/award_delete.html' % TEMPLATE_BASE, dict(
        badge=badge, award=award
    ), context_instance=RequestContext(request))


@login_required
def _do_claim(request, deferred_award):
    """Perform claim of a deferred award"""
    if not deferred_award.allows_claim_by(request.user):
        return HttpResponseForbidden('Claim denied')
    award = deferred_award.claim(request.user)
    if award:
        url = reverse('badger.awards_by_user',
                      args=(request.user.username,))
        return HttpResponseRedirect(url)


def _redirect_to_claimed_awards(awards, awards_ct):
    # Has this claim code already been used for awards?
    # If so, then a GET redirects to an award detail or list
    if awards_ct == 1:
        award = awards[0]
        url = reverse('badger.views.award_detail',
                      args=(award.badge.slug, award.id,))
        return HttpResponseRedirect(url)
    elif awards_ct > 1:
        award = awards[0]
        url = reverse('badger.views.awards_list',
                      args=(award.badge.slug,))
        return HttpResponseRedirect(url)


@require_http_methods(['GET', 'POST'])
def claim_deferred_award(request, claim_code=None):
    """Deferred award detail view"""
    if not claim_code:
        claim_code = request.REQUEST.get('code', '').strip()

    # Look for any awards that match this claim code.
    awards = Award.objects.filter(claim_code=claim_code)
    awards_ct = awards.count()

    # If this is a GET and there are awards matching the claim code, redirect
    # to the awards.
    if request.method == "GET" and awards_ct > 0:
        return _redirect_to_claimed_awards(awards, awards_ct)

    # Try fetching a DeferredAward matching the claim code. If none found, then
    # make one last effort to redirect a POST to awards. Otherwise, 404
    try:
        deferred_award = DeferredAward.objects.get(claim_code=claim_code)
    except DeferredAward.DoesNotExist:
        if awards_ct > 0:
            return _redirect_to_claimed_awards(awards, awards_ct)
        else:
            raise Http404('No such claim code, %s' % claim_code)

    if not deferred_award.allows_detail_by(request.user):
        return HttpResponseForbidden('Claim detail denied')

    if request.method != "POST":
        grant_form = DeferredAwardGrantForm()
    else:
        grant_form = DeferredAwardGrantForm(request.POST, request.FILES)
        if not request.POST.get('is_grant', False) is not False:
            return _do_claim(request, deferred_award)
        else:
            if not deferred_award.allows_grant_by(request.user):
                return HttpResponseForbidden('Grant denied')
            if grant_form.is_valid():
                email = request.POST.get('email', None)
                deferred_award.grant_to(email=email, granter=request.user)
                messages.info(request, _('Award claim granted to %s') % email)
                url = reverse('badger.views.detail',
                              args=(deferred_award.badge.slug,))
                return HttpResponseRedirect(url)

    return render_to_response('%s/claim_deferred_award.html' % TEMPLATE_BASE, dict(
        badge=deferred_award.badge, deferred_award=deferred_award,
        grant_form=grant_form
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def claims_list(request, slug, claim_group, format="html"):
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_manage_deferred_awards_by(request.user):
        return HttpResponseForbidden()

    deferred_awards = badge.get_claim_group(claim_group) 

    if format == "pdf":
        from badger.printing import render_claims_to_pdf
        return render_claims_to_pdf(request, slug, claim_group,
                                    deferred_awards)

    return render_to_response('%s/claims_list.html' % TEMPLATE_BASE, dict(
        badge=badge, claim_group=claim_group,
        deferred_awards=deferred_awards
    ), context_instance=RequestContext(request))


@require_GET
def awards_by_user(request, username):
    """Badge awards by user"""
    user = get_object_or_404(User, username=username)
    awards = Award.objects.filter(user=user)
    return render_to_response('%s/awards_by_user.html' % TEMPLATE_BASE, dict(
        user=user, award_list=awards,
    ), context_instance=RequestContext(request))


@require_GET
def awards_by_badge(request, slug):
    """Badge awards by badge"""
    badge = get_object_or_404(Badge, slug=slug)
    awards = Award.objects.filter(badge=badge)
    return render_to_response('%s/awards_by_badge.html' % TEMPLATE_BASE, dict(
        badge=badge, awards=awards,
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def staff_tools(request):
    """HACK: This page offers miscellaneous tools useful to event staff.
    Will go away in the future, addressed by:
    https://github.com/lmorchard/django-badger/issues/35
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden()

    if request.method != "POST":
        grant_form = DeferredAwardMultipleGrantForm()
    else:
        if request.REQUEST.get('is_grant', False) is not False:
            grant_form = DeferredAwardMultipleGrantForm(request.POST, request.FILES)
            if grant_form.is_valid():
                email = grant_form.cleaned_data['email']
                codes = grant_form.cleaned_data['claim_codes']
                for claim_code in codes:
                    da = DeferredAward.objects.get(claim_code=claim_code)
                    da.grant_to(email, request.user)
                    messages.info(request, _('Badge "%s" granted to %s' %
                                             (da.badge, email)))
                url = reverse('badger.views.staff_tools')
                return HttpResponseRedirect(url)


    return render_to_response('%s/staff_tools.html' % TEMPLATE_BASE, dict(
        grant_form=grant_form
    ), context_instance=RequestContext(request))


@require_GET
def badges_by_user(request, username):
    """Badges created by user"""
    user = get_object_or_404(User, username=username)
    badges = Badge.objects.filter(creator=user)
    return render_to_response('%s/badges_by_user.html' % TEMPLATE_BASE, dict(
        user=user, badge_list=badges,
    ), context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def nomination_detail(request, slug, id, format="html"):
    """Show details on a nomination, provide for approval and acceptance"""
    badge = get_object_or_404(Badge, slug=slug)
    nomination = get_object_or_404(Nomination, badge=badge, pk=id)
    if not nomination.allows_detail_by(request.user):
        return HttpResponseForbidden()

    if request.method == "POST":
        action = request.POST.get('action', '')
        if action == 'approve_by':
            nomination.approve_by(request.user)
        elif action == 'accept':
            nomination.accept(request.user)
        elif action == 'reject_by':
            nomination.reject_by(request.user)
        return HttpResponseRedirect(reverse(
                'badger.views.nomination_detail',
                args=(slug, id)))

    return render_to_response('%s/nomination_detail.html' % TEMPLATE_BASE,
                              dict(badge=badge, nomination=nomination,),
                              context_instance=RequestContext(request))


@require_http_methods(['GET', 'POST'])
@login_required
def nominate_for(request, slug):
    """Submit nomination for a badge"""
    badge = get_object_or_404(Badge, slug=slug)
    if not badge.allows_nominate_for(request.user):
        return HttpResponseForbidden()

    if request.method != "POST":
        form = BadgeSubmitNominationForm()
    else:
        form = BadgeSubmitNominationForm(request.POST, request.FILES)
        if form.is_valid():
            emails = form.cleaned_data['emails']
            for email in emails:
                users = User.objects.filter(email=email)
                if not users:
                    # TODO: Need a deferred nomination mechanism for
                    # non-registered users.
                    pass
                else:
                    nominee = users[0]
                    try:
                        award = badge.nominate_for(nominee, request.user)
                        messages.info(request,
                            _('Nomination submitted for %s') % email)
                    except BadgeAlreadyAwardedException, e:
                        messages.info(request,
                            _('Badge already awarded to %s') % email)
                    except Exception, e:
                        messages.info(request,
                            _('Nomination failed for %s') % email)

            return HttpResponseRedirect(reverse('badger.views.detail',
                                                args=(badge.slug,)))

    return render_to_response('%s/badge_nominate_for.html' % TEMPLATE_BASE,
                              dict(form=form, badge=badge,),
                              context_instance=RequestContext(request))
@login_required
def notification(request):
    user = request.user
    das = DeferredAward.objects.filter(email=user.email)
    return render_to_response('%s/notification.html' % TEMPLATE_BASE,
                              dict(das=das,),
                              context_instance=RequestContext(request))
