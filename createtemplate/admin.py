import pkg_resources
import os
import datetime
import shutil
import subprocess
import errno
import gzip
import re
import json
from operator import itemgetter
from itertools import groupby
# cElementTree is C implementation and faster
# http://eli.thegreenplace.net/2012/03/15/processing-xml-in-python-with-elementtree/
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from trac.core import *
from trac.web.chrome import ITemplateProvider, add_script, add_notice, add_script_data
from trac.admin.api import IAdminPanelProvider
from trac.wiki.model import WikiPage
from trac.wiki.api import WikiSystem
from trac.ticket import model
from logicaordertracker.controller import LogicaOrderController
from trac.perm import DefaultPermissionStore, IPermissionRequestor, PermissionSystem
from trac.ticket import Priority
from trac.attachment import Attachment
from trac.config import PathOption

from simplifiedpermissionsadminplugin.model import Group
from simplifiedpermissionsadminplugin.simplifiedpermissions import SimplifiedPermissions
from mailinglistplugin.model import Mailinglist
from createtemplate.api import ProjectTemplateAPI
from tracremoteticket.web_ui import RemoteTicketSystem 

# Author: Danny Milsom <danny.milsom@cgi.com>

class GenerateTemplate(Component):
    """Generates files which can be used by other projects as a base template"""

    template_dir_path = PathOption('project_templates', 'template_dir',
                    doc="The default path for the project template directory")

    implements(IPermissionRequestor, IAdminPanelProvider, ITemplateProvider)

    # IPermissionRequestor methods

    def get_permission_actions(self):
        return ["PROJECT_TEMPLATE_CREATE"]

    # IAdminPanelProvider

    def get_admin_panels(self, req):
        if 'PROJECT_TEMPLATE_CREATE' in req.perm:
            yield ('templates', ('Project Templates'),
           'create_template', ('Create Template'))

    def render_admin_panel(self, req, category, page, path_info):
        if page == 'create_template':

            # we always need to load JS regardless of POST or GET
            add_script(req, 'createtemplate/js/create_template_admin.js')

            # we also need to let JS know what templates currently exist
            # so we can validate client side
            template_api = ProjectTemplateAPI(self.env)
            all_templates = template_api.get_all_templates()
            add_script_data(req, { 'usedNames':all_templates })

            # find templates for this project and place them into data dict
            # as a list of dicts so we can display on page
            templates = []
            for template in all_templates:
                template_data = template_api.get_template_information(template)
                if template_data.get('project') == self.env.project_name:
                    templates.append(template_data)

            # sort template order based on created date
            templates = sorted(templates, key=itemgetter('created'))

            data = {
                    'name': self.env.project_name, 
                    'templates': templates
                    }

            # Send all available options to the template
            data['tpl_components'] = (("wiki", "Wiki pages and attachments"),
                                      ("ticket", "Ticket types, workflows, "
                                                 "custom fields, components, "
                                                 "priorities and versions"),
                                      ("archive", "Archive folder structure"),
                                      ("milestone", "Milestones"),
                                      ("list", "Mailing lists"),
                                      ("group", "Groups and permissions"))

            if req.method == 'POST':

                template_name = req.args.get('template_name')

                # server side check that there is a template name
                # there is jquery validation server side too
                if not template_name:
                    add_notice(req, "Please enter a template name")
                    return 'template_admin.html', data

                # check the template name input so its alphanumeric seperated by hyphens
                # we don't allow special characters etc
                complied_regex = re.compile(RemoteTicketSystem.PROJECTID_RE)
                if not complied_regex.match(template_name):
                    add_warning(req, "Please enter a different template name. "
                                "It should only include alphanumeric characters "
                                "and hypens. Special characters and spaces are "
                                "not allowed.")
                    return 'template_admin.html', data

                # if there is already a template with the same name we prompt user for an alternative
                # we can catch this on client side with JS too
                template_path = os.path.join(self.template_dir_path, template_name)
                try:
                    os.mkdir(template_path)
                    self.log.debug("Created directory for project template at", template_path)
                except OSError as exception:
                    if exception.errno == errno.EEXIST:
                        data.update({'failure':True,
                                     'template_name':template_name,
                                    })
                        return 'template_admin.html', data
                    raise

                # so far so good
                # we now call functions which create the XML template files
                # and append that data to a data dict we return to the template
                if 'template_components' in req.args:
                    options = req.args['template_components']

                    if 'wiki' in options:
                        data['wiki_pages'] = self.export_wiki_pages(template_path)
                        data['attachments'] = self.export_wiki_attachments(req, template_name)
                    if 'ticket' in options:
                        data['ticket_types'] = self.export_ticket_types(template_path)
                        data['workflows'] = self.export_workflows(req, template_path)
                        # we export priority, version and components if we export tickets
                        data['priority'] = self.export_priorites(template_path)
                        data['versions'] = self.export_versions(template_path)
                        data['components'] = self.export_components(template_path)
                    if 'archive' in options:
                        data['repos'] = self.export_file_archive(req, os.path.join(template_path, template_name + '.dump.gz'))
                    if 'group' in options:
                        # we import the group perms as part of the group export
                        data['groups'] = self.export_groups_and_permissions(template_path)
                    if 'list' in options:
                        data['lists'] = self.export_mailinglists(template_path)
                    if 'milestone' in options:
                        data['milestones'] = self.export_milestones(template_path)

                # create an info file to store the exact time of template
                # creation, username of template creator etc.
                self.create_template_info_file(req, template_name, template_path)

                data.update({'success':True,
                             'template_name':template_name,
                             })

                # we also need to add the new template to the list 
                # of templates we have for this project
                templates.append(template_api.get_template_information(template_name))

            return 'template_admin.html', data

    def export_wiki_pages(self, template_path):
        """Export wiki page data into a wiki.xml file.
        
        Get data for each wiki page that has not been deleted and place
        that inside an new XML tree. When we've finished building the tree, 
        create a new XML file called wiki.xml to store the content.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # Get page names and create wiki page objects
        # get_pages() already excludes deleted pages
        wiki_names = WikiSystem(self.env).get_pages()
        if wiki_names:
            project_wiki = [WikiPage(self.env, wiki_page) for wiki_page in wiki_names]

            # create an XML tree using ElementTree
            root = ET.Element("wiki", project=self.env.project_name, date=datetime.date.today().isoformat())
            for wiki in project_wiki:
                # only export wiki pages with text
                if wiki.text:

                    # standard attributes
                    attribs = {
                        'name': wiki.name,
                        'readonly': str(wiki.readonly),
                    }

                    # we can't serialize None
                    if wiki.author:
                        attribs['author'] = wiki.author

                    page = ET.SubElement(root, "page", attribs).text = wiki.text
                    successful_exports.append(wiki.name)

            # create the actual xml file
            filename = os.path.join(template_path, 'wiki.xml')
            ET.ElementTree(root).write(filename)
            self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_wiki_attachments(self, req, template_name):
        """Export wiki attachent files into a new wiki attachment directory.

        Exports files attached to wiki pages. To do this we need
        to export the wiki attachment data and put that into an XML file, 
        plus we need to store the actual files in our template directory!
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # Get information about attachments
        # Not really a nice way to get all the attachments in trac/attachments
        attachments = list()
        for wiki_name in WikiSystem(self.env).get_pages():
            for attachment in Attachment.select(self.env, 'wiki', wiki_name):
                if attachment.exists:
                    attachments.append(attachment)

        # write this information to XML tree if there are attachments to export
        if attachments:
            self.log.info("Creating wiki attachment XML file for template archive")
            root = ET.Element("attachments", project=self.env.project_name, date=datetime.date.today().isoformat())
            for attachment in attachments:
                ET.SubElement(root, "attachment", name=attachment.filename, 
                                                  parent_id=attachment.parent_id,
                                                  size=str(attachment.size),
                                                  version=str(attachment.version)).text = attachment.description
                successful_exports.append(attachment.filename)

            # create the xml file
            filename = os.path.join(self.template_dir_path, template_name, "attachment.xml")
            ET.ElementTree(root).write(filename)
            self.log.info("File %s has been created at %s" % (filename, os.path.join(self.template_dir_path, template_name)))

            # copy the project attachments into our new directory
            attachment_dir_path = os.path.join(self.env.path, 'attachments', 'wiki')
            attachment_template_path = os.path.join(self.template_dir_path, template_name, 'attachments', 'wiki')

            # the directory we copy to can't exist before shutil.copytree()
            try:
                shutil.rmtree(attachment_template_path)
            except OSError as exception:
                # no directory to remove
                if exception.errno == errno.ENOENT:
                    self.log.debug("No workflow directory at %s to remove", attachment_template_path)

            # now copy the directory
            shutil.copytree(attachment_dir_path, attachment_template_path)
            self.log.info("Copied wiki attachments to %s", attachment_template_path)

            return successful_exports

    def export_ticket_types(self, template_path):
        """Export ticket types by saving type JSON data in ticket.xml file.
        
        Creates a dictionary where each key is a ticket type and the value
        is ticket type information. We then iterate over this to create a XML
        file using ElementTree lib.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        types = [ticket_type.name for ticket_type in model.Type.select(self.env)]

        ticket_types_dict = dict()
        controller = LogicaOrderController(self.env)
        for ticket_type in types:
            # using a _method() is a bit naughty
            ticket_types_dict[ticket_type] = controller._serialize_ticket_type(ticket_type)

        # create the XML tree
        self.log.info("Creating ticket type XML file for template archive")

        root = ET.Element("ticket_types", project=self.env.project_name, date=datetime.date.today().isoformat())
        for type_name, type_info in ticket_types_dict.iteritems():
            ET.SubElement(root, "type_name", name=type_name).text = type_info
            successful_exports.append(type_name)

        # create the xml file
        filename = os.path.join(template_path, 'ticket.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_workflows(self, req, template_path):
        """Export project workflows into a new template workflow directory.
        
        Takes all the project specific workflows and copies them into
        into a new template directory. It doesn't matter if these have the same
        name as default workflows, as the project specific workflow has 
        priority.

        We always expect workflows to be xml files.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # make a directory to hold workflows
        workflow_template_path = os.path.join(template_path, 'workflows')
        try:
            os.mkdir(workflow_template_path)
        except OSError as exception:
            # if it already exists remove and create it 
            if exception.errno == errno.EEXIST:
                shutil.rmtree(workflow_template_path)
                os.mkdir(workflow_template_path)
        self.log.info("Created a template workflow directory at %s", workflow_template_path)

        # copy the workflows into our new directory
        workflow_dir = os.path.join(self.env.path, 'workflows')
        try:
            for workflow in os.listdir(workflow_dir):
                if workflow.lower().endswith('.xml'):
                    full_file_name = os.path.join(workflow_dir, workflow)
                    if (os.path.isfile(full_file_name)):
                        shutil.copy(full_file_name, workflow_template_path)
                        self.log.info("%s moved to %s template directory", (workflow, workflow_template_path))
                        successful_exports.append(workflow)
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log.debug("No workflows to export from current project.")

        return successful_exports

    def export_priorites(self, template_path):
        """Export priority data into a new priority.xml file.

        Get the different ticket priority values from the enum table and
        save the result into a priority.xml file.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # create the XML tree
        self.log.info("Creating priority XML file for template archive")
        root = ET.Element("ticket_priority", project=self.env.project_name, date=datetime.date.today().isoformat())
        for priority in Priority.select(self.env):
            ET.SubElement(root, "priority_info", name=priority.name, value=str(priority.value))
            successful_exports.append(priority.name)

        # create the xml file
        filename = os.path.join(template_path, 'priority.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_versions(self, template_path):
        """Export version data into a new version.xml file.
        
        Get the different ticket version values from the version table using 
        the select class method and save the result into a XML file.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # create the XML tree
        self.log.info("Creating version XML file for template archive")
        root = ET.Element("ticket_versions", project=self.env.project_name, date=datetime.date.today().isoformat())
        for version in model.Version.select(self.env):
            # not exporting time as this is unlikely to be relevant 
            # to any new project using this template
            ET.SubElement(root, "version_info", name=version.name, description=version.description)
            successful_exports.append(version.name)

        # create the xml file
        filename = os.path.join(template_path, 'version.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_components(self, template_path):
        """Export component data into a new component.xml file.

        Get the different ticket component values from the component table 
        using the select class method and save the result into a XML file."""

        # a list to return to the template with info about transaction
        successful_exports = list()

        # create the XML tree
        self.log.info("Creating component XML file for template archive")
        root = ET.Element("ticket_components", project=self.env.project_name, date=datetime.date.today().isoformat())
        for component in model.Component.select(self.env):
            # we don't save the owner as that user might not be a member
            # of the new project
            ET.SubElement(root, "component_info", name=component.name, 
                          description=component.description)
            successful_exports.append(component.name)

        # create the xml file
        filename = os.path.join(template_path, 'component.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_file_archive(self, req, new_repo_path):
        """Export project file archive, saving it in the new template directory.

        For now we only deal with Subversion repositories. We won't support
        the export of Git repos - but we will come back to solve this 
        issue (probably via GIT clone) in a future release.

        We compress this file via gzip.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()
        old_repo_path = self.env.get_repository().repos.path

        try:
            # Dump the file archive at the latest version (-rHEAD)
            process = subprocess.Popen(['svnadmin', 'dump', '--quiet', '-rHEAD', old_repo_path],
                                       executable='/usr/bin/svnadmin',
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            output = gzip.GzipFile(new_repo_path, 'w')
            bs = 1024*1000
            while True:
                data = process.stdout.read(bs)
                if data:
                    output.write(data)
                else:
                    # any remaining stderr will be read in a moment by communicate()
                    break
                stderrdata = process.stderr.read()
                if stderrdata:
                    self.log.warning("stderr from svnadmin: %s", stderrdata)                    
            output.close()
            stdoutdata, stderrdata = process.communicate()
            if stderrdata:
                self.log.warning("stderr from svnadmin: %s", stderrdata)
            self.log.info("Dumped the file archive (return code %s) at %s into the project template directory", 
                          process.returncode, 
                          old_repo_path)
            successful_exports = [old_repo_path.split("/")[-1]]
        except OSError as exception:
            self.log.info("No subversion repository at the path %s. Unable to export file archive.", old_repo_path)
            self.log.debug(exception)
            add_notice(req, "No Subversion repository found. Unable to export the file archive.")

        return successful_exports

    def export_groups_and_permissions(self, template_path):
        """
        Export project group data, saving it into a new group.xml file.
        
        Puts a list of all internal membership groups and associated 
        permissions into an XML file. We ignore linked groups at the moment.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        # data needed to export groups and associated permissions
        group_sids = [sid for sid in SimplifiedPermissions(self.env).groups]
        all_perms = DefaultPermissionStore(self.env).get_all_permissions()
        domains = SimplifiedPermissions(self.env).domains
        # where can we get authenticated and anonymous from the API?
        # seems to be hard coded in define/verify_perms.py
        virtual_groups = ['authenticated', 'anonymous']
        groups_and_domains = group_sids + domains + virtual_groups

        # filter out rows from permissions table not realted to groups or domains
        export_perms = [p for p in sorted(all_perms, key=itemgetter(0)) 
                        if p[0] in groups_and_domains]

        # group perms by the username column (e.g. group name)
        perm_dict = {}
        for group, perms in groupby(export_perms, key=itemgetter(0)):
            perm_dict[group] = [p for p in perms]

        root = ET.Element("membership_group", 
                          project=self.env.project_name, 
                          date=datetime.date.today().isoformat())

        # tried to unify the handling of the group_info XML generation
        for group in [Group(self.env, sid) for sid in group_sids] + domains + virtual_groups
            if hasattr(group, 'external_group') and group.external_group:
                # we don't remember why we skip these
                continue
            group_element = ET.SubElement(root, "group_info", name=unicode(group))
            if hasattr(group, 'sid'):          group_element.attrib['sid'] = group.sid
            if hasattr(group, 'label'):        group_element.attrib['label'] = group.label
            if hasattr(group, 'description'):  group_element.text = group.description
            # this dictionary is keyed by the group's sid, which is
            # either group.sid for a SimplifiedPermissions Group, or
            # just 'group'
            for perm in perm_dict.get(group.sid if hasattr(group, 'sid') else group, []):
                ET.SubElement(group_element, "group_perms",
                              name=perm[0], action=perm[1])
            successful_exports.append(unicode(group))

        self.log.info("Creating membership group XML file for template archive")
        filename = os.path.join(template_path, 'group.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s", filename, template_path)

        return successful_exports

    def export_mailinglists(self, template_path):
        """Exports project mailing lists into mailinglist.xml"""

        # a list to return to the template with info about transaction
        successful_exports = list()

        self.log.info("Creating mailing list XML file for template archive")
        root = ET.Element("lists", project=self.env.project_name, date=datetime.date.today().isoformat())
        for ml in Mailinglist.select(self.env):
            ET.SubElement(root, "list_info", name=ml.name,
                                             email=ml.emailaddress,
                                             private=str(ml.private),
                                             postperm=ml.postperm,
                                             replyto=ml.replyto).text = ml.description
            successful_exports.append(ml.name)

        # save the xml file
        filename = os.path.join(template_path, 'mailinglist.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def export_milestones(self, template_path):
        """Exports all project milestones into a new milestone.xml file.

        This respects the new sub-milestone feature, so all parent and child 
        milestones are included. We also store the start, due and completed 
        dates, along with the milestone description.
        """

        # a list to return to the template with info about transaction
        successful_exports = list()

        self.env.log.info("Creating milestone XML file for template archive")
        root = ET.Element("milestones", project=self.env.project_name, date=datetime.date.today().isoformat())
        all_milestones = model.Milestone.select(self.env, include_children=True)
        for milestone in all_milestones:
            ms = ET.SubElement(root, "milestone_info", name=milestone.name)
            # we need to do some checking incase the attribute has a None type
            if milestone.start:
                ms.attrib['start'] = milestone.start.strftime("%Y-%m-%d")
            if milestone.due:
                ms.attrib['due'] = milestone.due.strftime("%Y-%m-%d")
            if milestone.completed:
                ms.attrib['completed'] = milestone.completed.strftime("%Y-%m-%d")
            if milestone.parent:
                ms.attrib['parent'] = milestone.parent
            if milestone.description:
                ms.text = milestone.description
            successful_exports.append(milestone.name)

        # save the xml file in the template directory
        filename = os.path.join(template_path, 'milestone.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        return successful_exports

    def create_template_info_file(self, req, template_name, template_path):
        """Creates a new json file which stores metadata about the template. 

        This metadta includes information including the author who invoked the
        create template event, the date the template was created and the 
        description given by the author of the template.
        """

        filename = os.path.join(template_path, "info.json")

        text = {
            'name': template_name,
            'project': self.env.project_name,
            'created': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'author': req.authname,
            'description': req.args['description']
        }
        try:
            f = file(filename, "w")
            f.write(json.dumps(text))
        except IOError:
            self.log.info("Unable to create new file info folder at %s", filename)

    # ITemplateProvider methods

    def get_htdocs_dirs(self):
        return [('createtemplate', pkg_resources.resource_filename(__name__,
                                                                'htdocs'))]

    def get_templates_dirs(self):
        return [pkg_resources.resource_filename(__name__, 'templates')]