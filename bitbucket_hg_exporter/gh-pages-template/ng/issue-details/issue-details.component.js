'use strict';

// Register `issueDetails` component, along with its associated controller and template
angular.
  module('issueDetails').
  component('issueDetails', {
    templateUrl: 'ng/issue-details/issue-details.template.html',
    controller: ['$http', '$routeParams', '$sce', '$rootScope', '$location', '$timeout', '$anchorScroll', function IssueDetailsController($http, $routeParams, $sce, $rootScope, $location, $timeout, $anchorScroll) {
        var self = this;

        //pagination info
        self.issueId = $routeParams.issueId;
        self.commentPage = $routeParams.pageId;
        self.project_slug = $routeParams.owner + '/' + $routeParams.project;
        self.mainHtml = "";
      
      
        $http.get($rootScope.projects[self.project_slug]['project_path']+'issues/'+self.issueId+'.json').then(function(response) {
            self.issue = response.data;
            self.mainHtml = $sce.trustAsHtml(self.issue['content']['html']);
        });

        $http.get($rootScope.projects[self.project_slug]['project_path']+'issues/'+self.issueId+'/comments_page='+self.commentPage+'.json').then(function(response) {
          self.comments = response.data;
          angular.forEach(self.comments['values'], function(value, index){
            self.comments['values'][index]['content']['html'] = $sce.trustAsHtml(self.comments['values'][index]['content']['html']);

            $http.get($rootScope.projects[self.project_slug]['project_path'] + 'issues/' + self.issueId + '/changes/' + self.comments['values'][index]['id'] + '.json').then(
              function (change_response) {
                self.comments['values'][index]['changes'] = change_response.data
              },
              function errorCallback(change_response) {
                self.comments['values'][index]['changes'] = {};
              }
            );
          });

          // Now that the comments are (about to be) loaded
          // scroll to the comment specified in the hash if there is one
          $timeout(function(){$anchorScroll($location.hash());});
      });

      // Recursively load all attachments
      self.attachments = []
      
      function get_next_attachments(path) {
        $http.get(path).then(function(response) {
          self.attachments.push(...response.data['values']);
          if (response.data['next']) {
            get_next_attachments(response.data['next']);
          }
        });
      }

      get_next_attachments($rootScope.projects[self.project_slug]['project_path']+'issues/'+self.issueId+'/attachments_page=1.json');
        
    }]
  });